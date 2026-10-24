import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.model_worker import (
    PersistentWorker,
    WorkerTimedOut,
    isolated_environment,
)
from tests.conftest import FixtureParser
from tests.qa_helpers import RecordingEmbedder, RecordingProvider, RecordingReranker

WORKER = """
import json
import os
import sys
import time
from pathlib import Path

count = 0
for line in sys.stdin:
    request = json.loads(line)
    if request.get("hang"):
        time.sleep(10)
        continue
    count += 1
    path = Path(request["response_path"])
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps({"ok": True, "pid": os.getpid(), "count": count}))
    temporary.replace(path)
"""


def wait_stopped(worker: PersistentWorker, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while worker.pid is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker.pid is None


def test_worker_reuses_one_process_then_releases_it_after_idle(tmp_path: Path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    worker = PersistentWorker("fixture", 0.15, command=[sys.executable, "-u", str(script)])
    response = tmp_path / "response.json"
    try:
        first = worker.request({}, response, 2)
        second = worker.request({}, response, 2)
        assert first["pid"] == second["pid"]
        assert (first["count"], second["count"]) == (1, 2)

        wait_stopped(worker)
        third = worker.request({}, response, 2)
        assert third["count"] == 1
        assert third["pid"] != first["pid"]
    finally:
        worker.close()
    assert worker.pid is None


def test_timeout_terminates_the_worker(tmp_path: Path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    worker = PersistentWorker("fixture", 60, command=[sys.executable, "-u", str(script)])
    try:
        with pytest.raises(WorkerTimedOut):
            worker.request({"hang": True}, tmp_path / "response.json", 0.1)
        assert worker.pid is None
    finally:
        worker.close()


def test_worker_environment_removes_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("RESEARCH_MODEL_KEY_CUSTOM", "secret")
    monkeypatch.setenv("SAFE_SETTING", "kept")
    environment = isolated_environment({"HF_HUB_OFFLINE": "1"})
    assert "DEEPSEEK_API_KEY" not in environment
    assert "RESEARCH_MODEL_KEY_CUSTOM" not in environment
    assert environment["SAFE_SETTING"] == "kept"
    assert environment["HF_HUB_OFFLINE"] == "1"


def test_api_shutdown_releases_all_model_workers(settings):
    class Parser(FixtureParser):
        closed = False

        def close(self):
            self.closed = True

    class Embedder(RecordingEmbedder):
        closed = False

        def close(self):
            self.closed = True

    class Reranker(RecordingReranker):
        closed = False

        def close(self):
            self.closed = True

    parser, embedder, reranker = Parser(), Embedder(), Reranker()
    app = create_app(settings, parser, embedder, RecordingProvider(), reranker)
    with TestClient(app, base_url="http://127.0.0.1"):
        assert not parser.closed and not embedder.closed and not reranker.closed
    assert parser.closed and embedder.closed and reranker.closed

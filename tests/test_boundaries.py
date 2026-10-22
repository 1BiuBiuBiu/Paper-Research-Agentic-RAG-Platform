import subprocess

import pytest

from app.core.errors import AppError
from app.parsing.runner import DoclingRunner
from tests.test_ingestion import upload


def test_chunked_oversized_body_rejected_before_multipart(settings):
    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.core.config import Settings
    from tests.conftest import FixtureParser

    small = Settings(data_dir=settings.data_dir, max_upload_bytes=128)
    with TestClient(create_app(small, FixtureParser())) as client:
        response = client.post("/documents", content=(b"x" * 4096 for _ in range(18)))
        assert response.status_code == 413 and response.json()["code"] == "upload_limit"
        assert client.post("/documents", headers={"host": "attacker.example"}).status_code == 400


def test_worker_timeout_bounded_and_secrets_not_inherited(monkeypatch, tmp_path):
    observed = {}

    def run(*args, **kwargs):
        observed.update(kwargs)
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setenv("EXAMPLE_API_KEY", "test-secret-must-not-reach-worker")
    monkeypatch.setenv("RESEARCH_MODEL_KEY_CUSTOM", "custom-secret-must-not-reach-worker")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AppError, match="解析超时") as error:
        DoclingRunner(1).parse(tmp_path / "input.pdf", tmp_path / "assets")
    assert error.value.code == "parse_timeout"
    assert "EXAMPLE_API_KEY" not in observed["env"] and observed["timeout"] == 1
    assert "RESEARCH_MODEL_KEY_CUSTOM" not in observed["env"]


def test_manual_empty_required_indicator_source(client, pdf_path):
    document_id = upload(client, pdf_path)["document"]["id"]
    url = f"/documents/{document_id}"
    client.patch(url + "/metadata", json={"venue_type": "journal", "venue": "Journal"})
    response = client.put(url + "/metric", json={"year": 2024, "value": 4.5, "source": "   "})
    assert response.status_code == 422
    assert client.get(url).json()["parsed"]["metric"]["status"] == "missing"


def test_failed_retry_does_not_corrupt_another_document(client, pdf_path, tmp_path):
    from scripts.make_fixture import make_fixture

    first = upload(client, pdf_path)["document"]
    second = upload(client, make_fixture(tmp_path / "other.pdf", title="Second"))["document"]
    original = client.get(f"/documents/{first['id']}").json()
    assert client.post(f"/documents/{second['id']}/retry").status_code == 409
    assert client.get(f"/documents/{first['id']}").json() == original


def test_busy_ingestion_returns_retryable_status(client, pdf_path):
    lock = client.app.state.ingestion.lock
    lock.acquire()
    try:
        response = client.post("/documents", files={"file": ("test.pdf", pdf_path.read_bytes())})
        assert response.status_code == 409 and response.json()["code"] == "ingestion_busy"
        assert client.get("/documents").json()["total"] == 0
    finally:
        lock.release()

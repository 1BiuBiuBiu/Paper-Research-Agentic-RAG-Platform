import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.errors import AppError
from app.core.model_worker import WorkerTimedOut
from app.domain.papers import Block, Box
from app.retrieval.embedding import BGEEmbedder
from tests.conftest import FixtureParser
from tests.qa_helpers import (
    RecordingEmbedder,
    RecordingProvider,
    RecordingReranker,
    qa_client,
    question,
    seed_document,
)


class TextParser(FixtureParser):
    """The shared fixture parses as a title only; indexing needs one citable text block."""

    def parse(self, source: Path, assets: Path):
        paper = super().parse(source, assets)
        paper.blocks = [
            Block(
                id="body",
                page=1,
                order=0,
                kind="text",
                raw_text="Aurora wavelength method evidence.",
                text="Aurora measured the wavelength with a local method for the automatic index.",
                bbox=Box(left=48, top=90, right=550, bottom=140),
            )
        ]
        return paper


def upload(client: TestClient, pdf: Path) -> dict:
    response = client.post(
        "/documents", files={"file": ("paper.pdf", pdf.read_bytes(), "application/pdf")}
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


def wait_status(client: TestClient, identifier: str, expected: str, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        status = client.get(f"/documents/{identifier}/index").json()
        if status["status"] == expected:
            return status
        time.sleep(0.05)
    raise AssertionError(status)


class GatedEmbedder(RecordingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    def encode(self, texts):
        self.gate.wait(10)
        return super().encode(texts)


class FailableEmbedder(RecordingEmbedder):
    fail = True

    def encode(self, texts):
        if self.fail:
            raise AppError("embedding_unavailable", "Unavailable test model", 503)
        return super().encode(texts)


def auto_client(settings, embedder):
    app = create_app(
        replace(settings, auto_index=True),
        TextParser(),
        embedder,
        RecordingProvider(),
        RecordingReranker(),
    )
    return TestClient(app, base_url="http://127.0.0.1")


def test_a_successful_parse_queues_the_build_and_the_page_sees_building(settings, pdf_path):
    embedder = GatedEmbedder()
    with auto_client(settings, embedder) as client:
        document = upload(client, pdf_path)
        assert document["status"] == "parsed"
        status = client.get(f"/documents/{document['id']}/index").json()
        assert status["status"] == "building"
        embedder.gate.set()
        ready = wait_status(client, document["id"], "ready")
        assert ready["chunks"] > 0
        service = client.app.state.indexing
        manifest = service.indices.get(document["id"])
        assert manifest is not None and manifest.bm25_sha256
        assert service.bm25_path(manifest).is_file() and service.index_path(manifest).is_file()
        assert not service.pending(document["id"])


def test_a_failed_index_is_recorded_separately_and_can_be_retried(settings, pdf_path):
    embedder = FailableEmbedder()
    with auto_client(settings, embedder) as client:
        document = upload(client, pdf_path)
        failed = wait_status(client, document["id"], "failed")
        assert failed["error_code"] == "embedding_unavailable"
        assert client.get(f"/documents/{document['id']}").json()["status"] == "parsed"
        embedder.fail = False
        assert client.post(f"/documents/{document['id']}/index").json()["status"] == "ready"
        assert client.get(f"/documents/{document['id']}/index").json()["status"] == "ready"


def test_an_interrupted_auto_build_is_requeued_on_start(settings):
    client, _, _ = qa_client(settings)
    with client:
        document = seed_document(client.app.state.repository, "Aurora wavelength method evidence.")
        service = client.app.state.indexing
        service.mark_pending(document.id)
        assert service.resume_pending() == 1
        wait_status(client, document.id, "ready")
        assert not service.pending(document.id)


def test_index_busy_and_model_failure_do_not_publish_partial_index(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora wavelength was measured with a precise method."
        )
        service = client.app.state.indexing
        service.lock.acquire()
        try:
            response = client.post(f"/documents/{a.id}/index")
            assert response.status_code == 409 and response.json()["code"] == "index_busy"
        finally:
            service.lock.release()

        def fail(texts):
            raise AppError("embedding_unavailable", "Unavailable test model", 503)

        embedder.encode = fail
        assert client.post(f"/documents/{a.id}/index").status_code == 503
        assert service.indices.get(a.id) is None
        failed = client.get(f"/documents/{a.id}/index").json()
        assert failed["status"] == "failed" and failed["error_code"] == "embedding_unavailable"


def test_long_question_retrieval_encodes_all_segments(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora describes wavelength measurement in a method."
        )
        client.post(f"/documents/{a.id}/index")
        long_question = "Aurora " * 150 + "Which wavelength was measured?"
        response = client.post("/query", json=question(a.id, text=long_question))
        assert response.status_code == 200
        assert len(embedder.calls[-1]) > 1
        assert any("Which wavelength was measured?" in text for text in embedder.calls[-1])


class TimeoutWorker:
    def request(self, payload, response_path, timeout):
        raise WorkerTimedOut

    def close(self):
        pass


def test_embedding_worker_deadline_maps_to_app_error(settings):
    with pytest.raises(AppError) as error:
        BGEEmbedder(settings, TimeoutWorker()).encode(["Synthetic text"])
    assert error.value.code == "embedding_timeout"
    assert not list(settings.data_dir.glob("embedding-*"))

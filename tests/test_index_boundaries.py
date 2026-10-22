import subprocess

import pytest

from app.core.errors import AppError
from app.retrieval.embedding import BGEEmbedder
from tests.qa_helpers import qa_client, question, seed_document


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
        assert client.get(f"/documents/{a.id}/index").json()["status"] == "missing"


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


def test_embedding_worker_deadline_offline_and_credentials_filtered(settings, monkeypatch):
    seen = {}

    def timeout(*args, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired("embedding_worker", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-stay-outside-worker")
    monkeypatch.setenv("RESEARCH_MODEL_KEY_CUSTOM", "custom-key-must-stay-outside-worker")
    with pytest.raises(AppError) as error:
        BGEEmbedder(settings).encode(["Synthetic text"])
    assert error.value.code == "embedding_timeout"
    assert "DEEPSEEK_API_KEY" not in seen["env"]
    assert "RESEARCH_MODEL_KEY_CUSTOM" not in seen["env"]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert not list(settings.data_dir.glob("embedding-*"))

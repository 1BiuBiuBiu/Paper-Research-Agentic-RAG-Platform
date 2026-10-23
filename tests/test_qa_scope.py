import json
from dataclasses import replace
from uuid import uuid4

from app.api import create_app
from app.core.errors import AppError
from app.domain.qa import ModelAnswer
from tests.conftest import FixtureParser
from tests.qa_helpers import (
    RecordingEmbedder,
    RecordingReranker,
    qa_client,
    question,
    seed_document,
)


def test_a_b_scope_in_retrieval_model_input_sources_and_restart(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a = seed_document(
            repo,
            "Aurora measured a wavelength of 73 nanometers using the local method.",
            "Aurora abstract describes optical wavelength experiments.",
        )
        b = seed_document(repo, "Nebula measured B_ONLY_OBSERVATION 9981 using a separate method.")
        assert client.post(f"/documents/{a.id}/index").json()["status"] == "ready"
        assert all("B_ONLY_OBSERVATION" not in text for call in embedder.calls for text in call)
        assert client.get(f"/documents/{b.id}/index").json()["status"] == "missing"
        response = client.post("/query", json=question(a.id))
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["sources"] and all(s["document_id"] == a.id for s in answer["sources"])
        assert answer["sources"][0]["page"] == 1 and answer["sources"][0]["block_ids"] == [
            "original-block"
        ]
        sent = json.dumps(provider.calls[-1], ensure_ascii=False)
        assert (
            "Aurora abstract" in sent
            and "73 nanometers" in sent
            and "B_ONLY_OBSERVATION" not in sent
        )
        assert b.id not in sent and answer["used_web"] is False and answer["rewrite_count"] == 0
        ids = [s["id"] for s in answer["sources"]]
    restarted = create_app(
        settings, FixtureParser(), RecordingEmbedder(), provider, RecordingReranker()
    )
    from fastapi.testclient import TestClient

    with TestClient(restarted) as test:
        assert test.get(f"/documents/{a.id}/index").json()["status"] == "ready"
        response = test.post("/query", json=question(a.id, "pro"))
        assert response.status_code == 200
        assert [s["id"] for s in response.json()["sources"]] == ids
        assert provider.calls[-1][0] == "pro"
        with restarted.state.repository.connect() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 6


def test_invalid_empty_multiple_and_unready_scopes_never_call_model(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora describes a precise wavelength method."
        )
        for body in [
            {"question": "Question"},
            question(a.id, text="   "),
            {**question(a.id), "scope": {"type": "documents", "ids": []}},
            {**question(a.id), "scope": {"type": "documents", "ids": [a.id, str(uuid4())]}},
            {**question(a.id), "scope": {"type": "categories", "ids": [a.id]}},
            {**question(a.id), "model": "unapproved-model"},
        ]:
            assert client.post("/query", json=body).status_code == 422
        assert client.post("/query", json=question(str(uuid4()))).status_code == 404
        assert client.post("/query", json=question(a.id)).status_code == 409
        assert not provider.calls and not embedder.calls


def test_stale_metadata_corrupt_index_and_failed_build_preserve_old_generation(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        app = client.app
        a = seed_document(app.state.repository, "Aurora wavelength method measured stable results.")
        url = f"/documents/{a.id}/index"
        assert client.post(url).status_code == 200
        first = app.state.indexing.indices.get(a.id)
        assert first is not None
        app.state.indexing.index_path(first).write_bytes(b"corrupt")
        assert client.get(url).json()["status"] == "stale"
        assert client.post(url).status_code == 200
        second = app.state.indexing.indices.get(a.id)
        assert second is not None and second.generation != first.generation
        client.patch(f"/documents/{a.id}/metadata", json={"abstract": "Updated Aurora abstract"})
        assert client.get(url).json()["status"] == "stale"
        assert client.post("/query", json=question(a.id)).status_code == 409
        original = embedder.encode

        def fail(texts):
            raise AppError("simulated", "Expected failure", 503)

        embedder.encode = fail
        assert client.post(url).status_code == 503
        assert app.state.indexing.indices.get(a.id) == second
        embedder.encode = original
        assert client.post(url).json()["status"] == "ready"


def test_no_abstract_works_and_unknown_citations_rejected(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora measured a wavelength of 73 nanometers."
        )
        client.post(f"/documents/{a.id}/index")
        response = client.post("/query", json=question(a.id))
        assert response.json()["overview_used"] is False
        provider.result = ModelAnswer(
            answer="Invented [E99]", citations=["E99"], insufficient_evidence=False
        )
        response = client.post("/query", json=question(a.id))
        assert response.status_code == 502 and response.json()["code"] == "invalid_citations"
        provider.result = ModelAnswer(answer="No source", citations=[], insufficient_evidence=False)
        assert client.post("/query", json=question(a.id)).json()["code"] == "missing_citations"


def test_missing_key_never_encodes_query_or_calls_provider(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora describes a wavelength control method."
        )
        client.post(f"/documents/{a.id}/index")
        count = len(embedder.calls)
        provider.configured = False
        response = client.post("/query", json=question(a.id))
        assert response.status_code == 503 and response.json()["code"] == "model_not_configured"
        assert len(embedder.calls) == count and not provider.calls
        assert client.get("/models").json()["configured"] is False


def test_unrelated_question_refuses_after_bounded_correction_and_scoped_context_budget(settings):
    client, embedder, provider = qa_client(replace(settings, context_bytes=1000))
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora observed optical emissions and no other phenomena."
        )
        client.post(f"/documents/{a.id}/index")
        response = client.post("/query", json=question(a.id, text="Nebula"))
        assert response.status_code == 200 and response.json()["insufficient_evidence"]
        # Grade ① rejects the only chunk; the one targeted re-retrieval finds nothing new, so
        # the bounded turn refuses without spending a generation call.
        assert len(provider.calls) == 1 and not response.json()["sources"]
        assert response.json()["rewrite_count"] == 1
        response = client.post("/query", json=question(a.id, text="Aurora"))
        assert response.status_code == 200 and response.json()["context_bytes"] <= 1000


def test_source_changed_during_model_request_not_returned(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository,
            "Aurora found a stable wavelength using the control method.",
        )
        client.post(f"/documents/{a.id}/index")
        original = provider.complete

        def mutate(model, messages):
            def change(document):
                document.parsed.metadata.abstract = "Changed during model request"

            client.app.state.repository.update(a.id, change)
            return original(model, messages)

        provider.complete = mutate
        response = client.post("/query", json=question(a.id))
        assert response.status_code == 409 and response.json()["code"] == "document_changed"

import json

from app.domain.qa import ModelAnswer
from tests.qa_helpers import qa_client, question, seed_document


def test_rejected_credentials_not_echoed(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        response = client.post(
            "/query", json={"api_key": "never-echo-this-credential", "question": "test"}
        )
        assert response.status_code == 422
        assert "never-echo-this-credential" not in response.text and not provider.calls


def test_selected_b_scope_does_not_inherit_previous_a(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository,
            "Aurora A_ONLY_VALUE measured a wavelength of 73 nanometers.",
        )
        b = seed_document(
            client.app.state.repository,
            "Nebula B_ONLY_VALUE measured a wavelength of 9981 nanometers.",
        )
        for item in [a, b]:
            assert client.post(f"/documents/{item.id}/index").status_code == 200
        assert client.post("/query", json=question(a.id)).status_code == 200
        assert (
            client.post(
                "/query", json=question(b.id, text="What wavelength did Nebula measure?")
            ).status_code
            == 200
        )
        payload = json.dumps(provider.calls[-1], ensure_ascii=False)
        assert "B_ONLY_VALUE" in payload and "A_ONLY_VALUE" not in payload
        assert "A_ONLY_VALUE" not in provider.calls[-1][1][0]["content"]


def test_large_context_is_bounded_and_does_not_send_full_paper(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        body = "Aurora measured optical wavelength with a calibrated method. " * 400
        a = seed_document(client.app.state.repository, body, "研究摘要" * 2000)
        assert client.post(f"/documents/{a.id}/index").status_code == 200
        response = client.post("/query", json=question(a.id))
        assert response.status_code == 200
        payload = provider.calls[-1][1][1]["content"]
        assert body not in payload and len(payload.encode("utf-8")) < len(body.encode("utf-8"))
        assert response.json()["context_bytes"] <= settings.context_bytes
        assert len(response.json()["sources"]) <= 5


def test_provider_refusal_is_not_changed_into_success(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora describes a wavelength control method only."
        )
        client.post(f"/documents/{a.id}/index")
        provider.result = ModelAnswer(
            answer="原文没有足够信息。", citations=[], insufficient_evidence=True
        )
        result = client.post("/query", json=question(a.id)).json()
        assert result["insufficient_evidence"] is True and result["sources"] == []


def test_combined_references_and_mathematical_brackets_are_supported(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora wavelength has a precise value. " * 50
        )
        client.post(f"/documents/{a.id}/index")
        provider.result = ModelAnswer(
            answer="The normalized values lie in [0,1]. [E1, E2]",
            citations=["E1", "E2"],
            insufficient_evidence=False,
        )
        response = client.post("/query", json=question(a.id))
        assert response.status_code == 200
        assert {source["source_id"] for source in response.json()["sources"]} == {"E1", "E2"}

import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.errors import AppError
from app.domain.agent import AgentTurnInput
from app.domain.qa import ModelAnswer
from tests.conftest import FixtureParser
from tests.qa_helpers import RecordingEmbedder, model_response, qa_client, seed_document


def create_chat(client):
    response = client.post("/conversations", json={})
    assert response.status_code == 201
    return response.json()["id"]


def turn(scope, revision=0, question="Compare Aurora and Nebula wavelength methods.", **kwargs):
    return {
        "request_id": str(uuid4()),
        "expected_revision": revision,
        "question": question,
        "scope": scope,
        **kwargs,
    }


def send(client, conversation, body):
    return client.post(f"/conversations/{conversation}/turns", json=body)


def test_multi_paper_history_idempotency_and_restart(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a = seed_document(
            repo, "Aurora measured FIRST_A 73 nanometers with the local wavelength method."
        )
        b = seed_document(
            repo, "Nebula measured SECOND_B 98 nanometers with another wavelength method."
        )
        c = seed_document(repo, "OUTSIDE_ONLY never belongs to the requested research scope.")
        for doc in (a, b):
            assert client.post(f"/documents/{doc.id}/index").status_code == 200
        scope = {"kind": "documents", "document_ids": [a.id, b.id]}
        provider.result = ModelAnswer(
            answer="A [E1], B [E2]", citations=["E1", "E2"], insufficient_evidence=False
        )
        chat = create_chat(client)
        first = turn(scope)
        count = len(embedder.calls)
        response = send(client, chat, first)
        assert response.status_code == 200, response.text
        answer = response.json()
        assert set(answer["resolved_document_ids"]) == {a.id, b.id}
        assert set(answer["context_document_ids"]) == {a.id, b.id}
        assert {s["document_id"] for s in answer["sources"]} == {a.id, b.id}
        assert len(embedder.calls) == count + 1
        assert c.id not in json.dumps(provider.calls) and "OUTSIDE_ONLY" not in json.dumps(
            provider.calls
        )
        second = send(client, chat, turn(scope, 1, "它们有什么不同？"))
        assert second.status_code == 200 and second.json()["history_turns"] == 1
        assert provider.calls[-1][1][1]["role"] == "user"
        historical = provider.calls[-1][1][2]
        assert historical["role"] == "assistant" and "[E1]" not in historical["content"]
        before = len(provider.calls)
        assert send(client, chat, first).json() == answer
        assert len(provider.calls) == before
        assert send(client, chat, turn(scope, 0)).status_code == 409
    restarted = create_app(settings, FixtureParser(), RecordingEmbedder(), provider)
    with TestClient(restarted) as fresh:
        saved = fresh.get(f"/conversations/{chat}").json()
        assert saved["revision"] == 2 and len(saved["turns"]) == 2
        assert saved["turns"][0]["response"]["sources"] == answer["sources"]


def test_folder_membership_snapshot_resets_history_and_changes_abort(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a = seed_document(
            repo, "Aurora wavelength method reports a clear measurement of 73 nanometers."
        )
        b = seed_document(
            repo, "Nebula wavelength method reports a clear measurement of 98 nanometers."
        )
        for d in (a, b):
            client.post(f"/documents/{d.id}/index")
        folder = client.post("/categories", json={"name": "Topic"}).json()["id"]
        client.post("/documents/move", json={"document_ids": [a.id], "category_id": folder})
        scope = {"kind": "folder", "folder_id": folder}
        chat = create_chat(client)
        assert (
            send(
                client, chat, turn(scope, question="FIRST_HISTORY_MARK Aurora wavelength?")
            ).status_code
            == 200
        )
        client.post("/documents/move", json={"document_ids": [b.id], "category_id": folder})
        response = send(client, chat, turn(scope, 1))
        assert response.status_code == 200
        assert response.json()["history_reset"] and response.json()["history_turns"] == 0
        assert "FIRST_HISTORY_MARK" not in json.dumps(provider.calls[-1])
        original = provider.complete

        def mutate(model, messages):
            client.post("/documents/move", json={"document_ids": [b.id], "category_id": None})
            return original(model, messages)

        provider.complete = mutate
        body = turn(scope, 2)
        response = send(client, chat, body)
        assert response.status_code == 409 and response.json()["code"] == "scope_changed"
        saved = client.get(f"/conversations/{chat}").json()
        assert saved["revision"] == 2 and saved["turns"][-1]["status"] == "failed"
        assert saved["turns"][-1]["response"] is None
        provider.complete = original
        assert send(client, chat, body).status_code == 200
        assert client.get(f"/conversations/{chat}").json()["revision"] == 3


def test_invalid_empty_unready_and_sample_scope_never_calls_model(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        a = seed_document(
            client.app.state.repository, "Aurora wavelength method has useful original evidence."
        )
        chat = create_chat(client)
        folder = client.post("/categories", json={"name": "Empty"}).json()["id"]
        assert (
            client.post("/agent/scope", json={"kind": "folder", "folder_id": folder}).status_code
            == 422
        )
        assert (
            client.post(
                "/agent/scope", json={"kind": "folder", "folder_id": str(uuid4())}
            ).status_code
            == 404
        )
        assert (
            send(client, chat, turn({"kind": "documents", "document_ids": []})).status_code == 422
        )
        assert (
            send(
                client, chat, turn({"kind": "documents", "document_ids": [a.id, str(uuid4())]})
            ).status_code
            == 404
        )
        assert (
            send(client, chat, turn({"kind": "documents", "document_ids": [a.id]})).status_code
            == 409
        )
        client.app.state.repository.update(a.id, lambda d: setattr(d, "is_sample", True))
        assert (
            client.post(
                "/agent/scope", json={"kind": "documents", "document_ids": [a.id]}
            ).status_code
            == 422
        )
        assert not provider.calls and not embedder.calls


def test_busy_recovery_retry_and_request_conflict(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        provider.result = ModelAnswer(
            answer="General chat", citations=[], insufficient_evidence=False
        )
        chat = create_chat(client)
        body = turn({"kind": "chat"})
        repo = client.app.state.conversations.repository
        assert repo.begin(chat, AgentTurnInput.model_validate(body)) is None
        assert send(client, chat, body).json()["code"] == "conversation_busy"
        assert send(client, chat, turn({"kind": "chat"})).status_code == 409
        repo.recover_interrupted()
        original = provider.complete

        def rename_during_request(model, messages):
            client.patch(f"/conversations/{chat}", json={"title": "Human title"})
            return original(model, messages)

        provider.complete = rename_during_request
        assert send(client, chat, body).status_code == 200
        assert repo.get(chat)["title"] == "Human title"
        assert not embedder.calls and len(provider.calls) == 1
        assert send(client, chat, {**body, "question": "changed payload"}).status_code == 409
        other = create_chat(client)
        assert send(client, other, body).status_code == 409
        with pytest.raises(AppError):
            repo.finish(chat, body["request_id"], {})
        assert repo.get(chat)["revision"] == 1


def test_custom_api_connection_payload_key_isolation_and_disable(settings, monkeypatch):
    client, embedder, provider = qa_client(settings)
    secret = "test-only-not-a-real-credential"
    monkeypatch.setenv("RESEARCH_MODEL_KEY_UNIT", secret)
    with client:
        config = {
            "name": "Test Gateway",
            "base_url": "https://gateway.example/v1",
            "api_key_env": "RESEARCH_MODEL_KEY_UNIT",
            "models": ["custom-model"],
        }
        connection = client.post("/connections", json=config).json()
        assert connection["configured"] and secret not in json.dumps(connection)
        calls = []

        def handler(request):
            calls.append(request)
            assert request.url == httpx.URL("https://gateway.example/v1/chat/completions")
            assert request.headers["authorization"] == "Bearer " + secret
            payload = json.loads(request.content)
            assert payload["model"] == "custom-model"
            assert "thinking" not in payload and "reasoning_effort" not in payload
            data = model_response(answer="Compatible answer", citations=[])
            data["model"] = "custom-served"
            return httpx.Response(200, json=data)

        client.app.state.registry.transport = httpx.MockTransport(handler)
        chat = create_chat(client)
        body = turn({"kind": "chat"}, connection_id=connection["id"], model="custom-model")
        response = send(client, chat, body)
        assert response.status_code == 200 and response.json()["served_model"] == "custom-served"
        assert secret not in response.text
        assert secret not in client.get("/connections").text
        assert secret not in client.get(f"/conversations/{chat}").text
        assert (
            send(
                client,
                chat,
                turn({"kind": "chat"}, 1, connection_id=connection["id"], model="invented"),
            ).status_code
            == 422
        )
        assert (
            client.put(
                f"/connections/{connection['id']}", json={**config, "enabled": False}
            ).status_code
            == 200
        )
        assert (
            send(
                client,
                chat,
                turn({"kind": "chat"}, 1, connection_id=connection["id"], model="custom-model"),
            ).status_code
            == 409
        )
        assert len(calls) == 1 and not embedder.calls


def test_connection_validation_and_unauthenticated_local_api(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        config = {
            "name": "Local",
            "base_url": "http://127.0.0.1:9999/v1",
            "requires_key": False,
            "models": ["local-model"],
            "json_mode": False,
        }
        for update in [
            {"base_url": "http://external.example/v1"},
            {"base_url": "https://user:secret@example.com"},
            {"base_url": "https://example.com?key=x"},
            {"base_url": "https://example.com:bad"},
            {"api_key_env": "AWS_SECRET_ACCESS_KEY"},
            {"api_key": "forbidden"},
        ]:
            assert client.post("/connections", json={**config, **update}).status_code == 422
        connection = client.post("/connections", json=config).json()

        def handler(request):
            assert "authorization" not in request.headers
            assert "response_format" not in json.loads(request.content)
            return httpx.Response(200, json=model_response(answer="Local", citations=[]))

        client.app.state.registry.transport = httpx.MockTransport(handler)
        response = send(
            client,
            create_chat(client),
            turn({"kind": "chat"}, connection_id=connection["id"], model="local-model"),
        )
        assert response.status_code == 200

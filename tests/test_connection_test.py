import json

import httpx
import pytest

from app.domain.qa import ModelAnswer
from tests.qa_helpers import model_response, qa_client
from tests.test_agent_ui import app_ui, nav


def config(**updates):
    return {
        "name": "Synthetic Gateway",
        "base_url": "https://gateway.example/v1",
        "requires_key": False,
        "models": ["one", "two"],
        **updates,
    }


def success(request):
    return httpx.Response(200, json=model_response(answer="OK", citations=[]))


def test_draft_probe_fixed_prompt_bounded_each_model_and_no_save(settings):
    client, _, _ = qa_client(settings)
    with client:
        seen = []

        def handler(request):
            seen.append(request)
            return success(request)

        client.app.state.registry.transport = httpx.MockTransport(handler)
        response = client.post("/connection-tests/draft", json={"connection": config()})
        assert response.status_code == 200 and response.json()["status"] == "passed"
        assert len(seen) == 2 and len(client.get("/connections").json()["items"]) == 1
        assert [json.loads(r.content)["model"] for r in seen] == ["one", "two"]
        for request in seen:
            payload = json.loads(request.content)
            assert payload["max_tokens"] == 96 and payload["stream"] is False
            assert str(request.url) == "https://gateway.example/v1/chat/completions"
            assert (
                len(payload["messages"]) == 2
                and "Connection test" in payload["messages"][1]["content"]
            )
        assert response.json()["tested_at"] and "answer" not in response.json()


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_failed_probe_preserves_existing_config_and_sanitizes_response(settings, status):
    client, _, _ = qa_client(settings)
    with client:
        old = client.post("/connections", json=config()).json()
        client.app.state.registry.transport = httpx.MockTransport(
            lambda request: httpx.Response(status, text="SENSITIVE-REMOTE-BODY")
        )
        response = client.post(
            "/connection-tests/save",
            json={
                "identifier": old["id"],
                "connection": config(base_url="https://changed.example/v1"),
            },
        )
        assert response.status_code in (429, 502) and "SENSITIVE-REMOTE-BODY" not in response.text
        assert client.app.state.connections.get(old["id"])["base_url"] == old["base_url"]


def test_test_then_save_and_missing_credential_makes_no_network_call(settings, monkeypatch):
    client, _, _ = qa_client(settings)
    with client:
        requests = []

        def handler(request):
            requests.append(request)
            return success(request)

        client.app.state.registry.transport = httpx.MockTransport(handler)
        monkeypatch.setenv("RESEARCH_MODEL_KEY_MISSING", "")
        missing = config(requires_key=True, api_key_env="RESEARCH_MODEL_KEY_MISSING")
        assert (
            client.post("/connection-tests/save", json={"connection": missing}).status_code == 503
        )
        assert not requests and len(client.get("/connections").json()["items"]) == 1
        response = client.post("/connection-tests/save", json={"connection": config()})
        assert response.status_code == 200 and response.json()["connection"]["enabled"]
        assert len(requests) == 2 and len(client.get("/connections").json()["items"]) == 2


def test_invalid_json_contract_rejected_and_failure_does_not_save(settings):
    client, _, _ = qa_client(settings)
    with client:
        client.app.state.registry.transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=model_response(answer="wrong", citations=[]))
        )
        response = client.post("/connection-tests/save", json={"connection": config()})
        assert response.status_code == 502 and response.json()["code"] == "connection_probe_failed"
        assert len(client.get("/connections").json()["items"]) == 1


def test_saved_builtin_only_on_explicit_button_click_and_not_on_rerun(settings, monkeypatch):
    client, _, provider = qa_client(settings)
    with client:
        provider.result = ModelAnswer(answer="OK", citations=[], insufficient_evidence=False)
        app = app_ui(client, monkeypatch).run()
        nav(app, "设置")
        assert not provider.calls
        next(b for b in app.button if b.key == "test-existing").click().run()
        assert not app.exception and len(provider.calls) == 1 and app.success
        app.run()
        assert len(provider.calls) == 1


def test_custom_editor_tests_before_save(settings, monkeypatch):
    client, _, _ = qa_client(settings)
    with client:
        seen = []

        def handler(request):
            seen.append(request)
            return success(request)

        client.app.state.registry.transport = httpx.MockTransport(handler)
        app = app_ui(client, monkeypatch).run()
        nav(app, "设置")
        next(t for t in app.text_input if t.label == "服务名称").input("Synthetic UI")
        next(t for t in app.text_input if t.label == "API Base URL").input(
            "https://gateway.example/v1"
        )
        app.text_area[0].input("one")
        next(c for c in app.checkbox if c.label == "需要 API Key").uncheck()
        next(b for b in app.button if b.label == "测试并保存服务商").click().run()
        assert not app.exception and app.success and len(seen) == 1
        assert client.get("/connections").json()["items"][-1]["name"] == "Synthetic UI"

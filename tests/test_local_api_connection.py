from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.ui_components import client
from app.ui_components.client import ConnectionFailure


@pytest.mark.parametrize(
    "base", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]
)
def test_loopback_api_and_assets_bypass_inherited_proxies_without_retries(monkeypatch, base):
    calls = []
    monkeypatch.setattr(client, "API_BASE", base)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: request("GET", url, **kw))
    assert client.api("GET", "/health") == {"status": "ok"}
    client.image_bytes("paper", "page-1.png")
    assert len(calls) == 2 and all(c[2]["trust_env"] is False for c in calls)


def test_remote_override_retains_environment_and_html_response_does_not_leak(monkeypatch):
    monkeypatch.setattr(client, "API_BASE", "https://configured.example")
    assert client.trust_environment() is True
    calls = []

    def bad_response(*args, **kwargs):
        calls.append(args)
        return httpx.Response(502, text="<html>private-proxy-detail</html>")

    monkeypatch.setattr(httpx, "request", bad_response)
    with pytest.raises(ConnectionFailure) as error:
        client.api("POST", "/conversations/example/turns", json={"question": "example"})
    assert "private-proxy-detail" not in str(error.value)
    assert len(calls) == 1


def test_connection_failure_has_manual_reload_without_replaying_actions(monkeypatch):
    from app.ui_components import library

    calls = []
    available = False

    def request(*args, **kwargs):
        calls.append(args)
        if not available:
            raise ConnectionFailure("Local API unavailable")
        if args[1] == "/categories":
            return {"items": [], "total": 0, "uncategorized_count": 0}
        return {"items": [], "total": 0}

    from app.ui_components import categories

    monkeypatch.setattr(library, "api", request)
    monkeypatch.setattr(categories, "api", request)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app/main.py").run()
    assert not app.exception and app.error
    assert next(b for b in app.button if b.label == "重新连接本机服务")
    assert all(c[0] == "GET" for c in calls)
    available = True
    next(b for b in app.button if b.label == "重新连接本机服务").click().run()
    assert not app.exception and not app.error
    assert all(c[0] == "GET" for c in calls)

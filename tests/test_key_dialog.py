import base64
import subprocess

import pytest
from dotenv import dotenv_values
from streamlit.testing.v1 import AppTest

from app.core.errors import AppError
from app.services import key_dialog
from scripts.save_model_key import save_key


def encoded(value):
    return base64.b64encode(value.encode()).decode()


def test_native_save_preserves_other_settings_and_does_not_echo_key(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text(
        "# Keep comment\nOTHER_SETTING=keep\nDEEPSEEK_API_KEY='old'\n", encoding="utf-8"
    )
    secret = "test-only-key's_value#1"
    save_key(path, "DEEPSEEK_API_KEY", encoded(secret))
    values = dotenv_values(path)
    assert values["DEEPSEEK_API_KEY"] == secret
    assert values["OTHER_SETTING"] == "keep" and "# Keep comment" in path.read_text()
    assert capsys.readouterr() == ("", "")
    before = path.read_bytes()
    for variable, value in [("PATH", "unsafe"), ("DEEPSEEK_API_KEY", "a\nINJECT=b")]:
        with pytest.raises(ValueError):
            save_key(path, variable, encoded(value))
        assert path.read_bytes() == before


def test_launcher_only_passes_variable_and_script_path_without_shell_or_secret(monkeypatch):
    calls = []
    monkeypatch.setattr(key_dialog.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    result = key_dialog.open_key_dialog("DEEPSEEK_API_KEY")
    arguments, options = calls[0][0][0], calls[0][1]
    assert arguments[-2:] == ["-Variable", "DEEPSEEK_API_KEY"]
    assert "-STA" in arguments and "shell" not in options
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert result["status"] == "opened"
    with pytest.raises(AppError):
        key_dialog.open_key_dialog("PATH; write-host injected")
    assert len(calls) == 1


def test_dialog_route_uses_registered_service_reference_and_handles_missing_service(
    client, monkeypatch
):
    from app.routes import agent

    calls = []
    monkeypatch.setattr(
        agent,
        "open_key_dialog",
        lambda variable: calls.append(variable) or {"status": "opened", "message": "Native dialog"},
    )
    assert client.post("/connections/deepseek/key-dialog").json()["status"] == "opened"
    assert calls == ["DEEPSEEK_API_KEY"]
    assert client.post("/connections/nonexistent/key-dialog").status_code == 404
    assert client.post("/connections/tavily/key-dialog").json()["status"] == "opened"
    assert calls == ["DEEPSEEK_API_KEY", "TAVILY_API_KEY"]


def test_settings_button_requests_native_window_without_web_secret_field(monkeypatch):
    from app.ui_components import settings

    calls = []
    monkeypatch.setattr(
        settings,
        "api",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"message": "opened"},
    )
    app = AppTest.from_string("""
from app.ui_components.settings import credential_dialog
credential_dialog([{"id":"deepseek", "name":"DeepSeek", "requires_key":True,
                    "api_key_env":"DEEPSEEK_API_KEY"}])
""").run()
    assert not app.exception and not app.text_input and not app.text_area
    app.button[0].click().run()
    assert not app.exception
    assert calls == [(("POST", "/connections/deepseek/key-dialog"), {})]


def test_tavily_key_is_saved_only_in_temporary_backend_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OTHER_SETTING=keep\n", encoding="utf-8")
    save_key(path, "TAVILY_API_KEY", encoded("synthetic-tavily-key"))
    values = dotenv_values(path)
    assert values["TAVILY_API_KEY"] == "synthetic-tavily-key"
    assert values["OTHER_SETTING"] == "keep"

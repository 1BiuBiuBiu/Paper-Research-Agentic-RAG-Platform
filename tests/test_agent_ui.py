from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.domain.qa import ModelAnswer
from tests.qa_helpers import qa_client, seed_document


def app_ui(client, monkeypatch):
    from app.ui_components import (
        agent,
        categories,
        chat_navigation,
        detail,
        importer,
        library,
        model_picker,
        scope_picker,
        settings,
    )

    def request(method, path, **kwargs):
        from app.ui_components.client import ClientError

        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise ClientError(response.json()["message"])
        return response.json()

    for module in (
        agent,
        categories,
        chat_navigation,
        detail,
        importer,
        library,
        model_picker,
        scope_picker,
        settings,
    ):
        monkeypatch.setattr(module, "api", request)
    return AppTest.from_file(Path(__file__).resolve().parents[1] / "app/main.py")


def nav(app, name):
    next(b for b in app.button if b.key == "nav:" + name).click().run()


def open_scope(app):
    next(b for b in app.button if b.label == "资料范围").click().run()


def finish_scope(app):
    next(b for b in app.button if b.label == "完成选择").click().run()


def chat_scope(app):
    open_scope(app)
    next(c for c in app.checkbox if c.label == "通用对话（不使用资料）").check().run()
    finish_scope(app)


def test_agent_continuous_chat_multi_papers_and_folder_selection(settings, monkeypatch):
    client, embedder, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a = seed_document(
            repo, "Aurora measured wavelength 73 nanometers in a controlled local method."
        )
        b = seed_document(
            repo, "Nebula measured wavelength 98 nanometers in a separate local method."
        )
        for paper in (a, b):
            client.post(f"/documents/{paper.id}/index")
        folder = client.post("/categories", json={"name": "Research"}).json()["id"]
        client.post("/documents/move", json={"document_ids": [a.id, b.id], "category_id": folder})
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        assert not app.exception and not app.chat_input[0].disabled
        open_scope(app)
        next(b for b in app.button if b.label == "清空资料选择").click().run()
        next(r for r in app.radio if r.label == "浏览文件夹").set_value(folder).run()
        for paper in (a, b):
            next(c for c in app.checkbox if c.key.endswith(":" + paper.id)).check().run()
        finish_scope(app)
        assert not app.exception and not app.chat_input[0].disabled
        app.chat_input[0].set_value("Compare Aurora and Nebula wavelength methods").run()
        assert not app.exception
        assert len(app.chat_message) == 2
        app.chat_input[0].set_value("它们有什么不同？").run()
        assert not app.exception and len(app.chat_message) == 4
        chat = client.get("/conversations").json()["items"][0]["id"]
        stored = client.get(f"/conversations/{chat}").json()
        assert stored["revision"] == 2 and stored["turns"][-1]["response"]["history_turns"] == 1
        open_scope(app)
        next(r for r in app.radio if r.label == "浏览文件夹").set_value(folder).run()
        next(c for c in app.checkbox if c.label == "选择整个文件夹").check().run()
        finish_scope(app)
        app.chat_input[0].set_value("Aurora wavelength?").run()
        assert not app.exception
        stored = client.get(f"/conversations/{chat}").json()
        assert stored["turns"][-1]["request"]["scope"]["folder_ids"] == [folder]
        nav(app, "论文库")
        assert not app.exception and not app.chat_input
        nav(app, "Agent 平台")
        assert not app.exception and len(app.chat_message) == 6


def test_agent_failed_turn_retry_keeps_one_row(settings, monkeypatch):
    client, embedder, provider = qa_client(settings)
    with client:
        provider.result = ModelAnswer(
            answer="Wrong [E99]", citations=["E99"], insufficient_evidence=False
        )
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        chat_scope(app)
        app.chat_input[0].set_value("Hello").run()
        assert not app.exception and app.error
        provider.result = ModelAnswer(answer="Hello", citations=[], insufficient_evidence=False)
        next(b for b in app.button if b.label == "重试本轮").click().run()
        assert not app.exception
        chat = client.get("/conversations").json()["items"][0]["id"]
        stored = client.get(f"/conversations/{chat}").json()
        assert stored["revision"] == 1 and len(stored["turns"]) == 1
        assert stored["turns"][0]["status"] == "completed"


def test_settings_create_connection_and_agent_selects_it(settings, monkeypatch):
    monkeypatch.setenv("RESEARCH_MODEL_KEY_TEST_UI_UNSET", "")
    client, embedder, provider = qa_client(settings)
    with client:
        app = app_ui(client, monkeypatch).run()
        nav(app, "设置")
        assert not app.exception
        next(t for t in app.text_input if t.label == "服务名称").input("Other Gateway")
        next(t for t in app.text_input if t.label == "API Base URL").input(
            "https://gateway.example/v1"
        )
        app.text_area[0].input("other-model")
        next(t for t in app.text_input if t.label == "服务端密钥环境变量名").input(
            "RESEARCH_MODEL_KEY_TEST_UI_UNSET"
        )
        next(b for b in app.button if b.label == "保存服务商").click().run()
        assert not app.exception
        connection = client.get("/connections").json()["items"][-1]
        assert connection["name"] == "Other Gateway" and not connection["configured"]
        nav(app, "Agent 平台")
        next(s for s in app.selectbox if s.label == "模型").set_value(
            connection["id"] + "::other-model"
        ).run()
        chat_scope(app)
        assert not app.exception and app.chat_input[0].disabled
        assert not provider.calls


def test_project_create_chat_move_and_composer_model_default(settings, monkeypatch):
    client, _, provider = qa_client(settings)
    with client:
        client.put("/models/default", json={"connection_id": "deepseek", "model": "pro"})
        provider.result = ModelAnswer(answer="Hello", citations=[], insufficient_evidence=False)
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        assert next(s for s in app.selectbox if s.label == "模型").value == "deepseek::pro"
        assert not any(s.label == "API 服务" for s in app.selectbox)
        next(t for t in app.text_input if t.label == "项目名称").input("Literature study")
        next(b for b in app.button if b.label == "创建项目").click().run()
        assert not app.exception
        project = client.get("/projects").json()["items"][0]["id"]
        assert app.query_params["project"] == project
        next(b for b in app.button if b.label == "新建对话").click().run()
        chat_scope(app)
        app.chat_input[0].set_value("Hello from project").run()
        assert not app.exception and len(app.chat_message) == 2
        chat = client.get("/conversations").json()["items"][0]
        assert chat["project_id"] == project
        saved = client.get(f"/conversations/{chat['id']}").json()
        assert saved["turns"][0]["request"]["model"] == "pro"
        next(s for s in app.selectbox if s.label == "所属项目").set_value("unassigned")
        next(b for b in app.button if b.label == "保存对话设置").click().run()
        assert not app.exception and len(app.chat_message) == 2
        assert client.get(f"/conversations/{chat['id']}").json()["project_id"] is None


def test_disabled_selected_model_requires_new_selection_without_fallback(settings, monkeypatch):
    client, _, provider = qa_client(settings)
    with client:
        config = {
            "name": "Temporary gateway",
            "base_url": "http://127.0.0.1:9999/v1",
            "requires_key": False,
            "models": ["example-model"],
        }
        connection = client.post("/connections", json=config).json()["id"]
        client.put("/models/default", json={"connection_id": connection, "model": "example-model"})
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        chat_scope(app)
        assert not app.chat_input[0].disabled
        client.put(f"/connections/{connection}", json={**config, "enabled": False})
        app.run()
        assert not app.exception
        assert next(s for s in app.selectbox if s.label == "模型").value is None
        assert app.chat_input[0].disabled and not provider.calls


def test_settings_default_model_form_saves_first_selection(settings, monkeypatch):
    client, _, provider = qa_client(settings)
    with client:
        app = app_ui(client, monkeypatch).run()
        nav(app, "设置")
        button = next(b for b in app.button if b.label == "保存默认模型")
        assert not button.disabled
        next(s for s in app.selectbox if s.label == "新对话默认模型").set_value("deepseek::pro")
        button.click().run()
        assert not app.exception and app.success
        assert client.get("/model-catalog").json()["default"] == {
            "connection_id": "deepseek",
            "model": "pro",
        }
        nav(app, "Agent 平台")
        assert next(s for s in app.selectbox if s.label == "模型").value == "deepseek::pro"
        assert not provider.calls

"""User-visible combined selection and effective evidence scope contracts."""

import json
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from tests.qa_helpers import qa_client, seed_document
from tests.test_agent import create_chat, send, turn
from tests.test_agent_ui import app_ui, nav


def test_combined_folders_papers_deduplicate_and_never_search_outside(settings):
    client, _, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a, b, c, outside = [
            seed_document(repo, f"Aurora wavelength method {i} nanometers.") for i in range(4)
        ]
        folder1 = client.post("/categories", json={"name": "One"}).json()["id"]
        folder2 = client.post("/categories", json={"name": "Two"}).json()["id"]
        for paper, folder in [(a, folder1), (b, folder2), (outside, folder2)]:
            client.post("/documents/move", json={"document_ids": [paper.id], "category_id": folder})
        scope = {
            "kind": "mixed",
            "document_ids": [a.id, a.id, b.id],
            "folder_ids": [folder1, folder1],
            "uncategorized": True,
        }
        preview = client.post("/agent/scope", json=scope)
        assert preview.status_code == 200 and preview.json()["count"] == 3
        assert {d["id"] for d in preview.json()["items"]} == {a.id, b.id, c.id}
        # Add a second folder, then remove it: outside appears only while explicitly in the union.
        more = client.post("/agent/scope", json={**scope, "folder_ids": [folder1, folder2]}).json()
        assert more["count"] == 4
        for paper in (a, b, c):
            client.post(f"/documents/{paper.id}/index")
        result = send(client, create_chat(client), turn(scope))
        assert result.status_code == 200, result.text
        assert set(result.json()["resolved_document_ids"]) == {a.id, b.id, c.id}
        assert outside.id not in json.dumps(provider.calls)
        assert client.post("/agent/scope", json={"kind": "mixed"}).status_code == 422
        assert (
            client.post("/agent/scope", json={**scope, "folder_ids": [str(uuid4())]}).status_code
            == 404
        )
        assert (
            client.post("/agent/scope", json={"kind": "chat", "folder_ids": [folder1]}).status_code
            == 422
        )


def test_combined_unique_limit_is_enforced(settings):
    client, _, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        folder = client.post("/categories", json={"name": "Large"}).json()["id"]
        papers = [seed_document(repo, f"Synthetic bounded scope {i}") for i in range(101)]
        client.post(
            "/documents/move",
            json={"document_ids": [p.id for p in papers[:100]], "category_id": folder},
        )
        scope = {"kind": "mixed", "document_ids": [papers[0].id], "folder_ids": [folder]}
        assert client.post("/agent/scope", json=scope).json()["count"] == 100
        response = client.post("/agent/scope", json={**scope, "document_ids": [papers[-1].id]})
        assert response.status_code == 422 and response.json()["code"] == "scope_limit"
        assert not provider.calls


@pytest.mark.parametrize("folder_first", [False, True])
def test_ui_select_in_either_order_and_remove_independently(settings, monkeypatch, folder_first):
    from tests.test_agent_ui import finish_scope, open_scope

    client, _, provider = qa_client(settings)
    with client:
        a = seed_document(client.app.state.repository, "Aurora wavelength method.")
        b = seed_document(client.app.state.repository, "Nebula wavelength method.")
        folder = client.post("/categories", json={"name": "Selected folder"}).json()["id"]
        client.post("/documents/move", json={"document_ids": [b.id], "category_id": folder})
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        open_scope(app)
        next(b for b in app.button if b.label == "清空资料选择").click().run()
        for group in ["folder", "paper"] if folder_first else ["paper", "folder"]:
            next(r for r in app.radio if r.label == "浏览文件夹").set_value(
                folder if group == "folder" else "uncategorized"
            ).run()
            if group == "folder":
                next(c for c in app.checkbox if c.label == "选择整个文件夹").check().run()
            else:
                next(c for c in app.checkbox if c.key.endswith(":" + a.id)).check().run()
        assert not app.exception
        assert any("已选 2 篇文献" in c.value for c in app.caption)
        next(r for r in app.radio if r.label == "浏览文件夹").set_value("uncategorized").run()
        next(c for c in app.checkbox if c.key.endswith(":" + a.id)).uncheck().run()
        assert any("已选 1 篇文献" in c.value for c in app.caption)
        next(b for b in app.button if b.label == "清空资料选择").click().run()
        finish_scope(app)
        assert not app.exception and app.chat_input[0].disabled
        nav(app, "论文库")
        nav(app, "Agent 平台")
        open_scope(app)
        assert any("已选 0 篇文献" in c.value for c in app.caption)
        assert not provider.calls


def test_clear_does_not_reapply_saved_request(monkeypatch):
    from app.ui_components import scope_picker

    monkeypatch.setattr(
        scope_picker,
        "load_documents",
        lambda params: [
            {"id": "a", "title": "A", "filename": "a.pdf"},
            {"id": "b", "title": "B", "filename": "b.pdf"},
        ],
    )
    monkeypatch.setattr(
        scope_picker, "api", lambda *args, **kwargs: {"items": [{"id": "f", "name": "Folder"}]}
    )
    app = AppTest.from_string("""
import streamlit as st
from app.ui_components.scope_picker import scope_picker
scope = scope_picker('saved-chat', {'kind':'mixed','document_ids':['a','b'],'folder_ids':['f']})
st.text(str(scope))
""").run()
    next(c for c in app.checkbox if c.key.endswith(":a")).uncheck().run()
    next(b for b in app.button if b.label == "清空资料选择").click().run()
    for enabled in [True, False]:
        next(c for c in app.checkbox if c.label == "通用对话（不使用资料）").set_value(
            enabled
        ).run()
    app.run()
    assert not app.exception and app.text[-1].value == "None"


def test_pagination_group_actions_and_exclusions_survive_reopen(settings, monkeypatch):
    from tests.test_agent_ui import finish_scope, open_scope

    client, _, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        papers = [seed_document(repo, f"Synthetic listed paper {i}") for i in range(11)]
        folder = client.post("/categories", json={"name": "Other folder"}).json()["id"]
        client.post(
            "/documents/move", json={"document_ids": [papers[-1].id], "category_id": folder}
        )
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        open_scope(app)
        next(b for b in app.button if b.label == "清空资料选择").click().run()
        next(c for c in app.checkbox if c.label == "选择本页").check().run()
        assert any("已选 8 篇文献" in c.value for c in app.caption)
        next(s for s in app.selectbox if s.label == "文献列表页").set_value(2).run()
        assert len([c for c in app.checkbox if c.key.startswith("agent:file:")]) == 2
        assert all(not c.value for c in app.checkbox if c.key.startswith("agent:file:"))
        next(c for c in app.checkbox if c.label == "选择本页").check().run()
        assert any("已选 10 篇文献" in c.value for c in app.caption)
        next(c for c in app.checkbox if c.label == "选择整个文件夹").check().run()
        one = next(c for c in app.checkbox if c.key.startswith("agent:file:"))
        removed = one.key.rsplit(":", 1)[1]
        one.uncheck().run()
        assert any("已选 9 篇文献" in c.value for c in app.caption)
        finish_scope(app)
        assert any("当前范围：9 篇论文" in c.value for c in app.caption)
        open_scope(app)
        next(s for s in app.selectbox if s.label == "文献列表页").set_value(2).run()
        assert not next(c for c in app.checkbox if c.key.endswith(":" + removed)).value
        next(c for c in app.checkbox if c.label == "选择所有文件夹").check().run()
        assert any("已选 11 篇文献" in c.value for c in app.caption)
        next(r for r in app.radio if r.label == "浏览文件夹").set_value(folder).run()
        assert next(c for c in app.checkbox if c.key.endswith(":" + papers[-1].id)).value
        next(c for c in app.checkbox if c.label == "选择所有文件夹").uncheck().run()
        assert any("已选 0 篇文献" in c.value for c in app.caption)
        assert not provider.calls


def test_exclusions_apply_to_folder_union_and_final_context(settings):
    client, _, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a = seed_document(repo, "Aurora kept wavelength method result.")
        b = seed_document(repo, "EXCLUDED_ONLY should never be sent.")
        client.post(f"/documents/{a.id}/index")
        scope = {"kind": "mixed", "uncategorized": True, "excluded_document_ids": [b.id]}
        preview = client.post("/agent/scope", json=scope)
        assert {d["id"] for d in preview.json()["items"]} == {a.id}
        answer = send(client, create_chat(client), turn(scope))
        assert answer.status_code == 200 and answer.json()["resolved_document_ids"] == [a.id]
        assert "EXCLUDED_ONLY" not in json.dumps(provider.calls)
        all_excluded = {**scope, "excluded_document_ids": [a.id, b.id]}
        assert client.post("/agent/scope", json=all_excluded).json()["code"] == "empty_scope"

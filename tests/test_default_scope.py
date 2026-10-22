import json

from tests.qa_helpers import qa_client, seed_document
from tests.test_agent_ui import app_ui, finish_scope, nav, open_scope


def test_new_agent_defaults_to_all_without_opening_selection(settings, monkeypatch):
    client, _, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        papers = [seed_document(repo, f"Aurora wavelength method {i}") for i in range(2)]
        for paper in papers:
            client.post(f"/documents/{paper.id}/index")
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        assert any("默认范围：全部论文 · 2 篇" in c.value for c in app.caption)
        assert not app.chat_input[0].disabled and not provider.calls
        app.chat_input[0].set_value("Aurora wavelength method?").run()
        chat = client.get("/conversations").json()["items"][0]["id"]
        saved = client.get(f"/conversations/{chat}").json()["turns"][-1]
        assert saved["request"]["scope"]["kind"] == "all"
        assert set(saved["response"]["resolved_document_ids"]) == {p.id for p in papers}


def test_default_all_can_exclude_and_clear_without_becoming_all_again(settings, monkeypatch):
    client, _, provider = qa_client(settings)
    with client:
        papers = [
            seed_document(client.app.state.repository, f"Synthetic default {i}") for i in range(2)
        ]
        app = app_ui(client, monkeypatch).run()
        nav(app, "Agent 平台")
        open_scope(app)
        assert all(c.value for c in app.checkbox if c.key.startswith("agent:file:"))
        next(c for c in app.checkbox if c.key.endswith(":" + papers[0].id)).uncheck().run()
        assert any("已选 1 篇文献" in c.value for c in app.caption)
        finish_scope(app)
        open_scope(app)
        assert not next(c for c in app.checkbox if c.key.endswith(":" + papers[0].id)).value
        next(b for b in app.button if b.label == "清空资料选择").click().run()
        finish_scope(app)
        nav(app, "论文库")
        nav(app, "Agent 平台")
        assert app.chat_input[0].disabled
        assert not any("默认范围：全部论文" in c.value for c in app.caption)
        assert not provider.calls


def test_all_scope_resolves_new_documents_exclusions_and_hides_samples(settings):
    client, _, provider = qa_client(settings)
    with client:
        repo = client.app.state.repository
        a = seed_document(repo, "Synthetic A")
        sample = seed_document(repo, "Synthetic sample")
        repo.update(sample.id, lambda d: setattr(d, "is_sample", True))
        scope = {"kind": "all"}
        assert client.post("/agent/scope", json=scope).json()["count"] == 1
        b = seed_document(repo, "Synthetic B")
        response = client.post("/agent/scope", json=scope).json()
        assert {d["id"] for d in response["items"]} == {a.id, b.id}
        remaining = client.post(
            "/agent/scope", json={**scope, "excluded_document_ids": [a.id]}
        ).json()
        assert [d["id"] for d in remaining["items"]] == [b.id]
        assert (
            client.post("/agent/scope", json={**scope, "document_ids": [a.id]}).status_code == 422
        )
        assert sample.id not in json.dumps(response) and not provider.calls

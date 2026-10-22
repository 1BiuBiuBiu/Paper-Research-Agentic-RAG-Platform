from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import create_app
from app.repositories.papers import PaperRepository
from tests.conftest import FixtureParser
from tests.qa_helpers import RecordingEmbedder, qa_client, seed_document


def test_schema_v4_migration_preserves_existing_unassigned_chat_and_document(settings):
    papers = PaperRepository(settings.data_dir / "library.sqlite3")
    paper = seed_document(papers, "Untouched paper before project migration.")
    original = paper.model_dump_json()
    identifier = str(uuid4())
    with papers.connect() as db:
        db.execute(
            "CREATE TABLE conversations (id TEXT PRIMARY KEY,title TEXT NOT NULL,"
            "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER "
            "NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0)"
        )
        db.execute(
            "INSERT INTO conversations VALUES(?,?,?, ?,0,0)",
            (identifier, "Previous title", "2026-01-01", "2026-01-01"),
        )
        db.execute("PRAGMA user_version=4")
    client, _, _ = qa_client(settings)
    with client:
        chat = client.get(f"/conversations/{identifier}").json()
        assert chat["title"] == "Previous title" and chat["project_id"] is None
        assert client.app.state.repository.get(paper.id).model_dump_json() == original
        with papers.connect() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 5


def test_projects_group_move_rename_and_restart_preserve_turns_and_papers(settings):
    client, embedder, provider = qa_client(settings)
    with client:
        paper = seed_document(client.app.state.repository, "Original unique document content.")
        original = client.app.state.repository.get(paper.id).model_dump_json()
        first = client.post("/projects", json={"name": "Alpha"}).json()["id"]
        second = client.post("/projects", json={"name": "Beta"}).json()["id"]
        chat = client.post("/conversations", json={"project_id": first}).json()
        chat_id = chat["id"]
        repo = client.app.state.conversations.repository
        # Persist an interrupted record; grouping must preserve the exact prior turn.
        from app.domain.agent import AgentTurnInput

        repo.begin(
            chat_id,
            AgentTurnInput.model_validate(
                {
                    "request_id": str(uuid4()),
                    "expected_revision": 0,
                    "question": "Original question",
                    "scope": {"kind": "chat"},
                }
            ),
        )
        repo.recover_interrupted()
        turns = client.get(f"/conversations/{chat_id}").json()["turns"]
        assert client.post(f"/projects/{first}/archive").status_code == 409
        assert (
            client.patch(f"/conversations/{chat_id}", json={"title": "My chat"}).json()[
                "project_id"
            ]
            == first
        )
        moved = client.patch(f"/conversations/{chat_id}", json={"project_id": second}).json()
        assert moved["title"] == "My chat" and moved["turns"] == turns
        assert client.get("/conversations", params={"project_id": first}).json()["items"] == []
        assert len(client.get("/conversations", params={"project_id": second}).json()["items"]) == 1
        assert client.get("/conversations", params={"unassigned": True}).json()["items"] == []
        assert client.get("/projects").json()["items"][1]["count"] == 1
        assert client.patch(f"/projects/{second}", json={"name": "Renamed"}).status_code == 200
        assert client.app.state.repository.get(paper.id).model_dump_json() == original
        assert not provider.calls and not embedder.calls
    with TestClient(create_app(settings, FixtureParser(), RecordingEmbedder(), provider)) as fresh:
        saved = fresh.get(f"/conversations/{chat_id}").json()
        assert saved["project_id"] == second and saved["turns"] == turns
        detached = fresh.patch(f"/conversations/{chat_id}", json={"project_id": None}).json()
        assert detached["project_id"] is None and detached["turns"] == turns
        assert fresh.post(f"/projects/{second}/archive").status_code == 200
        assert fresh.post("/conversations", json={"project_id": second}).status_code == 404
        assert fresh.get("/projects", params={"archived": True}).json()["items"][0]["id"] == second
        assert fresh.post(f"/projects/{second}/restore").status_code == 200
        assert fresh.post("/conversations", json={"project_id": second}).status_code == 201


def test_project_validation_atomic_membership_and_paging(settings):
    client, _, _ = qa_client(settings)
    with client:
        project = client.post("/projects", json={"name": "Alpha"}).json()["id"]
        assert client.post("/projects", json={"name": " alpha "}).status_code == 409
        for name in ("", "   ", "a" * 81):
            assert client.post("/projects", json={"name": name}).status_code == 422
        chat = client.post("/conversations", json={}).json()["id"]
        assert client.get(f"/conversations/{chat}").json()["project_id"] is None
        for body in ({}, {"title": None}, {"title": " "}, {"project_id": "not-a-uuid"}):
            assert client.patch(f"/conversations/{chat}", json=body).status_code == 422
        assert (
            client.patch(
                f"/conversations/{chat}",
                json={
                    "title": "Not committed",
                    "project_id": str(uuid4()),
                },
            ).status_code
            == 404
        )
        assert client.get(f"/conversations/{chat}").json()["title"] == "新对话"
        assert (
            client.get(
                "/conversations", params={"project_id": project, "unassigned": True}
            ).status_code
            == 422
        )
        assert client.get("/conversations", params={"project_id": str(uuid4())}).status_code == 404
        assert client.get("/projects", params={"offset": 1, "limit": 1}).json() == {
            "items": [],
            "total": 1,
        }


def test_model_catalog_unique_provider_identity_default_persists_without_model_call(settings):
    client, embedder, provider = qa_client(settings)
    config = {
        "name": "Gateway",
        "base_url": "http://127.0.0.1:9999/v1",
        "requires_key": False,
        "models": ["flash", "another-model"],
    }
    with client:
        connection = client.post("/connections", json=config).json()["id"]
        catalog = client.get("/model-catalog").json()
        assert catalog["default"] is None
        builtin = next(m for m in catalog["items"] if m["id"] == "deepseek::flash")
        gateway = next(m for m in catalog["items"] if m["id"] == connection + "::flash")
        assert builtin["model_id"] == "deepseek-flash" and gateway["model_id"] == "flash"
        default = {"connection_id": connection, "model": "another-model"}
        assert client.put("/models/default", json=default).json() == default
        assert (
            client.put("/models/default", json={**default, "model": "missing"}).status_code == 422
        )
        assert client.get("/model-catalog").json()["default"] == default
        assert not provider.calls and not embedder.calls
    with TestClient(create_app(settings, FixtureParser(), RecordingEmbedder(), provider)) as fresh:
        assert fresh.get("/model-catalog").json()["default"] == default
        assert (
            fresh.put(f"/connections/{connection}", json={**config, "enabled": False}).status_code
            == 200
        )
        assert fresh.put("/models/default", json=default).status_code == 409
        assert all(
            not m["enabled"]
            for m in fresh.get("/model-catalog").json()["items"]
            if m["connection_id"] == connection
        )

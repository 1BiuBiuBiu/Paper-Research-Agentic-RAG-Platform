import hashlib
import sqlite3
from uuid import uuid4

from app.domain.papers import Document, utc_now
from app.repositories.categories import CategoryRepository
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.retrieval.chunks import fingerprint
from tests.qa_helpers import qa_client, question, seed_document
from tests.test_ingestion import upload


def category(client, name="Research"):
    r = client.post("/categories", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_category_names_normalization_rename_archive_restore(client):
    identifier = category(client, " ＲＡＧ ")
    assert client.get("/categories").json()["items"][0]["name"] == "RAG"
    assert client.post("/categories", json={"name": "rag"}).status_code == 409
    for name in ["", "   ", "未分类", "a\nb", "x" * 81]:
        assert client.post("/categories", json={"name": name}).status_code == 422
    assert (
        client.patch(f"/categories/{identifier}", json={"name": "论文笔记"}).json()["name"]
        == "论文笔记"
    )
    assert client.post(f"/categories/{identifier}/archive").status_code == 200
    assert client.get("/categories").json()["items"] == []
    assert len(client.get("/categories?include_archived=true").json()["items"]) == 1
    assert client.post(f"/categories/{identifier}/restore").json()["id"] == identifier


def test_moves_are_atomic_and_do_not_change_pdf_parse_or_index(settings, pdf_path):
    client, embedder, provider = qa_client(settings)
    with client:
        identifier = upload(client, pdf_path)["document"]["id"]
        repo = client.app.state.repository

        def body(document):
            document.parsed.blocks[0].kind = "text"
            document.parsed.blocks[
                0
            ].text = "Aurora measured 73 nanometers in the local experiment."

        repo.update(identifier, body)
        assert client.post(f"/documents/{identifier}/index").status_code == 200
        before = repo.get(identifier)
        index = client.app.state.indexing.indices.get(identifier)
        source = settings.data_dir / "papers" / identifier / "original.pdf"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        a, b = category(client, "Topic A"), category(client, "Topic B")
        moved = client.post(
            "/documents/move", json={"document_ids": [identifier, identifier], "category_id": a}
        )
        assert moved.json()["moved"] == 1
        after = repo.get(identifier)
        assert (
            after.category_id == a
            and after.parsed == before.parsed
            and after.updated_at == before.updated_at
        )
        assert client.get(f"/documents/{identifier}/index").json()["status"] == "ready"
        assert client.app.state.indexing.indices.get(identifier) == index
        assert fingerprint(before, "model", "rev") == fingerprint(after, "model", "rev")
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        duplicate = upload(client, pdf_path, name="same-paper-renamed.pdf")
        assert duplicate["duplicate"] and duplicate["document"]["id"] == identifier
        assert duplicate["document"]["category_id"] == a
        assert len(list((settings.data_dir / "papers").glob("*/original.pdf"))) == 1
        assert client.post(f"/categories/{a}/archive").status_code == 409
        result = client.post(
            "/documents/move", json={"document_ids": [identifier, str(uuid4())], "category_id": b}
        )
        assert result.status_code == 404 and repo.get(identifier).category_id == a
        assert (
            client.post(
                "/documents/move", json={"document_ids": [identifier], "category_id": b}
            ).status_code
            == 200
        )
        assert client.get("/documents", params={"category_id": a}).json()["total"] == 0
        assert (
            client.get("/documents", params={"category_id": b}).json()["items"][0]["id"]
            == identifier
        )
        response = client.post("/query", json=question(identifier))
        assert response.status_code == 200 and all(
            s["document_id"] == identifier for s in response.json()["sources"]
        )
        assert (
            client.post(
                "/documents/move", json={"document_ids": [identifier], "category_id": None}
            ).status_code
            == 200
        )
        assert client.get("/documents?uncategorized=true").json()["total"] == 1
        restarted = PaperRepository(settings.data_dir / "library.sqlite3")
        assert restarted.get(identifier).category_id is None
        assert len(CategoryRepository(restarted).list()["items"]) == 2


def test_filter_and_search_happen_before_pagination(client):
    repo = client.app.state.repository
    docs = [seed_document(repo, f"Unique scientific experiment number {i}.") for i in range(65)]
    identifier = category(client)
    client.post(
        "/documents/move",
        json={"document_ids": [docs[0].id, docs[1].id], "category_id": identifier},
    )
    response = client.get("/documents", params={"category_id": identifier, "limit": 1, "offset": 1})
    assert response.json()["total"] == 2 and response.json()["items"][0]["id"] == docs[0].id
    client.patch(f"/documents/{docs[0].id}/metadata", json={"title": "Needle 100% match"})
    assert client.get("/documents", params={"q": "Needle"}).json()["total"] == 1
    assert client.get("/documents", params={"q": "100%"}).json()["total"] == 1
    assert client.get("/documents", params={"q": "%' OR 1=1 --"}).json()["total"] == 0
    repo.update(docs[1].id, lambda d: setattr(d, "is_sample", True))
    assert (
        client.get(
            "/documents", params={"category_id": identifier, "include_samples": False}
        ).json()["total"]
        == 1
    )
    assert client.get("/categories?include_samples=false").json()["items"][0]["paper_count"] == 1


def test_invalid_moves_and_category_filters_are_rejected(client, pdf_path):
    identifier = upload(client, pdf_path)["document"]["id"]
    folder = category(client)
    for body in [
        {"document_ids": [], "category_id": folder},
        {"document_ids": [identifier]},
        {"document_ids": [identifier], "category_id": "bad"},
    ]:
        assert client.post("/documents/move", json=body).status_code == 422
    assert (
        client.get("/documents", params={"category_id": folder, "uncategorized": True}).status_code
        == 422
    )
    assert client.get("/documents", params={"category_id": str(uuid4())}).status_code == 404
    client.post(f"/categories/{folder}/archive")
    assert (
        client.post(
            "/documents/move", json={"document_ids": [identifier], "category_id": folder}
        ).status_code
        == 409
    )
    assert client.get("/documents", params={"category_id": folder}).status_code == 409


def test_schema_v2_migrates_without_rewriting_document_body(settings):
    settings.data_dir.mkdir(parents=True)
    path = settings.data_dir / "library.sqlite3"
    d = Document(
        id=str(uuid4()),
        filename="old.pdf",
        sha256="oldhash",
        status="failed",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    raw = d.model_dump_json(exclude={"category_id"})
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,"
            "body TEXT NOT NULL)"
        )
        db.execute("INSERT INTO documents VALUES(?,?,?)", (d.id, d.sha256, raw))
        db.execute("PRAGMA user_version=2")
    repo = PaperRepository(path)
    IndexRepository(repo)
    assert repo.get(d.id).category_id is None
    with repo.connect() as db:
        assert db.execute("SELECT body FROM documents").fetchone()[0] == raw
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6

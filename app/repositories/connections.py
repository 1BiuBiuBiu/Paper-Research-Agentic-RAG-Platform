import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from app.core.errors import AppError
from app.domain.agent import ConnectionInput
from app.repositories.papers import PaperRepository


def credential(name: str | None) -> str:
    if not name:
        return ""
    local = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    return os.getenv(name, local.get(name) or "").strip()


class ConnectionRepository:
    def __init__(self, papers: PaperRepository) -> None:
        self.papers = papers
        with papers.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS model_connections (id TEXT PRIMARY "
                "KEY, name TEXT NOT NULL UNIQUE, body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS model_preferences (id INTEGER PRIMARY KEY "
                "CHECK(id=1), selection TEXT NOT NULL)"
            )

    def default_model(self) -> dict | None:
        with self.papers.connect() as db:
            row = db.execute("SELECT selection FROM model_preferences WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def set_default_model(self, selection: dict) -> None:
        with self.papers.connect() as db:
            db.execute(
                "INSERT INTO model_preferences VALUES(1,?) ON CONFLICT(id) "
                "DO UPDATE SET selection=excluded.selection",
                (json.dumps(selection),),
            )

    def save(self, body: ConnectionInput, identifier: str | None = None) -> dict:
        if identifier:
            self.get(identifier)
        identifier = identifier or str(uuid4())
        try:
            with self.papers.connect() as db:
                db.execute(
                    (
                        "INSERT INTO model_connections VALUES(?,?,?) ON CONFLICT(id) DO "
                        "UPDATE SET name=excluded.name,body=excluded.body"
                    ),
                    (identifier, body.name, body.model_dump_json()),
                )
        except sqlite3.IntegrityError:
            raise AppError("connection_exists", "模型服务名称已存在。", 409) from None
        return self.get(identifier)

    def get(self, identifier: str) -> dict:
        with self.papers.connect() as db:
            row = db.execute(
                "SELECT body FROM model_connections WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise AppError("connection_not_found", "模型服务不存在。", 404)
        body = ConnectionInput.model_validate_json(row[0])
        return {
            "id": identifier,
            **body.model_dump(),
            "configured": not body.requires_key or bool(credential(body.api_key_env)),
        }

    def list(self) -> list[dict]:
        with self.papers.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM model_connections ORDER BY rowid")]
        return [self.get(identifier) for identifier in ids]

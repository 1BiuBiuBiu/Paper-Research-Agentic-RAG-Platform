import sqlite3

from app.core.errors import AppError


def initialize_projects(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS chat_projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "normalized_name TEXT NOT NULL UNIQUE, archived INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    columns = {r[1] for r in db.execute("PRAGMA table_info(conversations)")}
    if "project_id" not in columns:
        db.execute(
            "ALTER TABLE conversations ADD COLUMN project_id TEXT "
            "REFERENCES chat_projects(id) ON DELETE RESTRICT"
        )
    db.execute("CREATE INDEX IF NOT EXISTS ix_chat_project ON conversations(project_id,updated_at)")
    if db.execute("PRAGMA user_version").fetchone()[0] < 5:
        db.execute("PRAGMA user_version=5")


def require_project(db: sqlite3.Connection, identifier: str | None) -> None:
    if (
        identifier is not None
        and not db.execute(
            "SELECT 1 FROM chat_projects WHERE id=? AND archived=0", (identifier,)
        ).fetchone()
    ):
        raise AppError("project_not_found", "项目不存在或已归档。", 404)

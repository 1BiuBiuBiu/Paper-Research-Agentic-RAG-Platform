import sqlite3
from uuid import uuid4

from app.core.errors import AppError
from app.domain.papers import utc_now
from app.repositories.papers import PaperRepository
from app.repositories.project_schema import require_project


class ProjectRepository:
    def __init__(self, papers: PaperRepository) -> None:
        self.papers = papers

    def list(self, offset: int = 0, limit: int = 100, *, archived: bool = False) -> dict:
        with self.papers.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM chat_projects WHERE archived=?", (int(archived),)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT p.id,p.name,p.updated_at,COUNT(c.id) FROM chat_projects p "
                "LEFT JOIN conversations c ON c.project_id=p.id AND c.archived=0 "
                "WHERE p.archived=? GROUP BY p.id ORDER BY p.created_at,p.id LIMIT ? OFFSET ?",
                (int(archived), limit, offset),
            ).fetchall()
        return {
            "items": [
                dict(zip(("id", "name", "updated_at", "count"), r, strict=True)) for r in rows
            ],
            "total": count,
        }

    def save(self, name: str, identifier: str | None = None) -> dict:
        now = utc_now()
        try:
            with self.papers.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if identifier:
                    require_project(db, identifier)
                    db.execute(
                        "UPDATE chat_projects SET name=?,normalized_name=?,updated_at=? WHERE id=?",
                        (name, name.casefold(), now, identifier),
                    )
                else:
                    identifier = str(uuid4())
                    db.execute(
                        "INSERT INTO chat_projects VALUES(?,?,?,0,?,?)",
                        (identifier, name, name.casefold(), now, now),
                    )
        except sqlite3.IntegrityError:
            raise AppError("project_exists", "项目名称已存在。", 409) from None
        return {"id": identifier, "name": name}

    def archive(self, identifier: str) -> dict:
        with self.papers.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            require_project(db, identifier)
            if db.execute(
                "SELECT 1 FROM conversations WHERE project_id=? AND archived=0", (identifier,)
            ).fetchone():
                raise AppError("project_not_empty", "请先将项目中的对话移出，再归档项目。", 409)
            db.execute("UPDATE chat_projects SET archived=1 WHERE id=?", (identifier,))
        return {"id": identifier, "archived": True}

    def restore(self, identifier: str) -> dict:
        with self.papers.connect() as db:
            updated = db.execute(
                "UPDATE chat_projects SET archived=0,updated_at=? WHERE id=? AND archived=1",
                (utc_now(), identifier),
            )
            if updated.rowcount != 1:
                raise AppError("project_not_found", "归档项目不存在。", 404)
        return {"id": identifier, "archived": False}

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import uuid4

from app.core.errors import AppError
from app.domain.categories import Category, CategoryInput
from app.domain.papers import utc_now

if TYPE_CHECKING:
    from app.repositories.papers import PaperRepository


class CategoryRepository:
    def __init__(self, papers: PaperRepository) -> None:
        self.papers = papers

    def list(self, *, include_archived: bool = False, include_samples: bool = True) -> dict:
        with self.papers.connect() as connection:
            rows = connection.execute(
                "SELECT c.id,c.name,c.archived,c.created_at,c.updated_at,COUNT(d.id) "
                "FROM categories c LEFT JOIN document_categories m ON c.id=m.category_id "
                "LEFT JOIN documents d ON d.id=m.document_id AND "
                "(? OR COALESCE(json_extract(d.body,'$.is_sample'),0)=0) "
                "WHERE (? OR c.archived=0) GROUP BY c.id ORDER BY c.created_at,c.id",
                (include_samples, include_archived),
            ).fetchall()
            uncategorized = connection.execute(
                "SELECT COUNT(*) FROM documents d LEFT JOIN document_categories m "
                "ON d.id=m.document_id WHERE m.document_id IS NULL "
                "AND (? OR COALESCE(json_extract(d.body,'$.is_sample'),0)=0)",
                (include_samples,),
            ).fetchone()[0]
        return {
            "items": [self._category(row) for row in rows],
            "uncategorized_count": uncategorized,
        }

    @staticmethod
    def _category(row: tuple) -> Category:
        return Category(
            id=row[0],
            name=row[1],
            archived=bool(row[2]),
            created_at=row[3],
            updated_at=row[4],
            paper_count=row[5],
        )

    def require(self, identifier: str, *, active: bool = True) -> Category:
        with self.papers.connect() as connection:
            row = connection.execute(
                "SELECT c.id,c.name,c.archived,c.created_at,c.updated_at,COUNT(m.document_id) "
                "FROM categories c LEFT JOIN document_categories m ON c.id=m.category_id "
                "WHERE c.id=? GROUP BY c.id",
                (identifier,),
            ).fetchone()
        if row is None:
            raise AppError("category_not_found", "分类不存在。", 404)
        category = self._category(row)
        if active and category.archived:
            raise AppError("category_archived", "此分类已归档，请先恢复。", 409)
        return category

    def create(self, name: str) -> Category:
        name = CategoryInput(name=name).name
        identifier, now = str(uuid4()), utc_now()
        try:
            with self.papers.connect() as connection:
                connection.execute(
                    "INSERT INTO categories VALUES(?,?,?,0,?,?)",
                    (identifier, name, name.casefold(), now, now),
                )
        except sqlite3.IntegrityError:
            raise AppError(
                "category_exists", "分类名称已存在（含归档分类），可重命名或恢复。", 409
            ) from None
        return self.require(identifier)

    def rename(self, identifier: str, name: str) -> Category:
        name = CategoryInput(name=name).name
        try:
            with self.papers.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT archived FROM categories WHERE id=?", (identifier,)
                ).fetchone()
                if row is None:
                    raise AppError("category_not_found", "分类不存在。", 404)
                if row[0]:
                    raise AppError("category_archived", "请先恢复此分类。", 409)
                connection.execute(
                    "UPDATE categories SET name=?,normalized_name=?,updated_at=? WHERE id=?",
                    (name, name.casefold(), utc_now(), identifier),
                )
        except sqlite3.IntegrityError:
            raise AppError("category_exists", "分类名称已存在。", 409) from None
        return self.require(identifier)

    def archive(self, identifier: str, *, archived: bool) -> Category:
        with self.papers.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute(
                "SELECT 1 FROM categories WHERE id=?", (identifier,)
            ).fetchone():
                raise AppError("category_not_found", "分类不存在。", 404)
            if (
                archived
                and connection.execute(
                    "SELECT 1 FROM document_categories WHERE category_id=?", (identifier,)
                ).fetchone()
            ):
                raise AppError("category_not_empty", "请先移走该分类中的论文，再归档。", 409)
            connection.execute(
                "UPDATE categories SET archived=?,updated_at=? WHERE id=?",
                (archived, utc_now(), identifier),
            )
        return self.require(identifier, active=False)

    def move(self, identifiers: Sequence[str], category_id: str | None) -> dict:
        identifiers = list(dict.fromkeys(identifiers))
        with self.papers.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if category_id is not None:
                target = connection.execute(
                    "SELECT archived FROM categories WHERE id=?", (category_id,)
                ).fetchone()
                if target is None:
                    raise AppError("category_not_found", "目标分类不存在。", 404)
                if target[0]:
                    raise AppError("category_archived", "目标分类已归档。", 409)
            placeholders = ",".join("?" for _ in identifiers)
            count = connection.execute(
                f"SELECT COUNT(*) FROM documents WHERE id IN ({placeholders})", identifiers
            ).fetchone()[0]
            if count != len(identifiers):
                raise AppError("document_not_found", "部分论文不存在，未移动任何论文。", 404)
            for identifier in identifiers:
                if category_id is None:
                    connection.execute(
                        "DELETE FROM document_categories WHERE document_id=?", (identifier,)
                    )
                else:
                    connection.execute(
                        "INSERT INTO document_categories VALUES(?,?) "
                        "ON CONFLICT(document_id) DO UPDATE SET category_id=excluded.category_id",
                        (identifier, category_id),
                    )
        return {"moved": len(identifiers), "category_id": category_id, "document_ids": identifiers}

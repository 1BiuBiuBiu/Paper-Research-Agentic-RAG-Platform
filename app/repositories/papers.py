import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import AppError
from app.domain.papers import Document, DocumentSummary, utc_now
from app.repositories.category_schema import initialize_categories


class PaperRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents "
                "(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, body TEXT NOT NULL)"
            )
            if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
                connection.execute("PRAGMA user_version=1")
            initialize_categories(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, document_id: str) -> Document:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT d.body,m.category_id FROM documents d LEFT JOIN document_categories m "
                "ON d.id=m.document_id WHERE d.id=?",
                (document_id,),
            ).fetchone()
        if not row:
            raise AppError("document_not_found", "论文不存在。", 404)
        return Document.model_validate_json(row[0]).model_copy(update={"category_id": row[1]})

    def by_hash(self, sha256: str) -> Document | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT body FROM documents WHERE sha256=?", (sha256,)
            ).fetchone()
        return self.get(Document.model_validate_json(row[0]).id) if row else None

    def save(self, document: Document) -> None:
        document.updated_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (document.id, document.sha256, document.model_dump_json(exclude={"category_id"})),
            )

    def update(self, document_id: str, mutation: Callable[[Document], None]) -> Document:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if not row:
                raise AppError("document_not_found", "论文不存在。", 404)
            document = Document.model_validate_json(row[0])
            membership = connection.execute(
                "SELECT category_id FROM document_categories WHERE document_id=?", (document_id,)
            ).fetchone()
            document.category_id = membership[0] if membership else None
            mutation(document)
            document.updated_at = utc_now()
            connection.execute(
                "UPDATE documents SET body=? WHERE id=?",
                (document.model_dump_json(exclude={"category_id"}), document_id),
            )
        return document

    def list(
        self,
        offset: int = 0,
        limit: int = 50,
        *,
        category_id: str | None = None,
        uncategorized: bool = False,
        include_samples: bool = True,
        search: str = "",
    ) -> tuple[list[DocumentSummary], int]:
        where, parameters = ["1=1"], []
        if category_id is not None:
            where.append("m.category_id=?")
            parameters.append(category_id)
        elif uncategorized:
            where.append("m.document_id IS NULL")
        if not include_samples:
            where.append("COALESCE(json_extract(d.body,'$.is_sample'),0)=0")
        if search.strip():
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(COALESCE(json_extract(d.body,'$.parsed.metadata.title'),'') || ' ' || "
                "json_extract(d.body,'$.filename') || ' ' || "
                "COALESCE(json_extract(d.body,'$.parsed.metadata.venue'),'')) LIKE ? ESCAPE '\\'"
            )
            parameters.append("%" + escaped + "%")
        joined = (
            " FROM documents d LEFT JOIN document_categories m ON d.id=m.document_id WHERE "
            + " AND ".join(where)
        )
        with self.connect() as connection:
            count = connection.execute("SELECT count(*)" + joined, parameters).fetchone()[0]
            rows = connection.execute(
                "SELECT d.body,m.category_id" + joined + " ORDER BY d.rowid DESC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        summaries: list[DocumentSummary] = []
        for row in rows:
            document = Document.model_validate_json(row[0])
            paper = document.parsed
            summaries.append(
                DocumentSummary(
                    id=document.id,
                    filename=document.filename,
                    status=document.status,
                    title=paper.metadata.title if paper else None,
                    venue=paper.metadata.venue if paper else None,
                    year=paper.metadata.publication_year if paper else None,
                    pages=len(paper.pages) if paper else 0,
                    error_message=document.error_message,
                    created_at=document.created_at,
                    is_sample=document.is_sample,
                    category_id=row[1],
                    authors=paper.metadata.authors if paper else [],
                )
            )
        return summaries, count

    def recover_interrupted(self) -> None:
        with self.connect() as connection:
            rows = connection.execute("SELECT body FROM documents").fetchall()
        for row in rows:
            document = Document.model_validate_json(row[0])
            if document.status == "parsing":
                document.status = "failed"
                document.error_code = "interrupted"
                document.error_message = "上次解析被中断，可以重试。"
                self.save(document)

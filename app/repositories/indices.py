from app.domain.qa import IndexManifest
from app.repositories.papers import PaperRepository


class IndexRepository:
    def __init__(self, papers: PaperRepository) -> None:
        self.papers = papers
        with papers.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS document_indices "
                "(document_id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            # Index failures are recorded apart from parse failures so they can be retried
            # without touching the parsed paper itself.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS document_index_failures "
                "(document_id TEXT PRIMARY KEY, code TEXT NOT NULL, message TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            if connection.execute("PRAGMA user_version").fetchone()[0] < 6:
                connection.execute("PRAGMA user_version=6")

    def get(self, document_id: str) -> IndexManifest | None:
        with self.papers.connect() as connection:
            row = connection.execute(
                "SELECT body FROM document_indices WHERE document_id=?", (document_id,)
            ).fetchone()
        return IndexManifest.model_validate_json(row[0]) if row else None

    def activate(self, manifest: IndexManifest) -> None:
        with self.papers.connect() as connection:
            connection.execute(
                "INSERT INTO document_indices VALUES (?, ?) ON CONFLICT(document_id) "
                "DO UPDATE SET body=excluded.body",
                (manifest.document_id, manifest.model_dump_json()),
            )

    def failure(self, document_id: str) -> tuple[str, str] | None:
        with self.papers.connect() as connection:
            row = connection.execute(
                "SELECT code, message FROM document_index_failures WHERE document_id=?",
                (document_id,),
            ).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def fail(self, document_id: str, code: str, message: str, updated_at: str) -> None:
        with self.papers.connect() as connection:
            connection.execute(
                "INSERT INTO document_index_failures VALUES (?, ?, ?, ?) "
                "ON CONFLICT(document_id) DO UPDATE SET code=excluded.code, "
                "message=excluded.message, updated_at=excluded.updated_at",
                (document_id, code, message, updated_at),
            )

    def clear_failure(self, document_id: str) -> None:
        with self.papers.connect() as connection:
            connection.execute(
                "DELETE FROM document_index_failures WHERE document_id=?", (document_id,)
            )

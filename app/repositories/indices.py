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
            if connection.execute("PRAGMA user_version").fetchone()[0] < 2:
                connection.execute("PRAGMA user_version=2")

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

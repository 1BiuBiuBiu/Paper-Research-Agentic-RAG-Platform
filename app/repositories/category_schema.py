import sqlite3


def initialize_categories(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS categories ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE, "
        "archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS document_categories ("
        "document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, "
        "category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE RESTRICT)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_category ON document_categories(category_id)"
    )
    if connection.execute("PRAGMA user_version").fetchone()[0] < 3:
        connection.execute("PRAGMA user_version=3")

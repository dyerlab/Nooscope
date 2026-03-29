from __future__ import annotations

import sqlite3
import struct
import time


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vaults (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            root_path   TEXT NOT NULL,
            description TEXT,
            created_at  REAL DEFAULT (unixepoch())
        );

        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY,
            vault_id     INTEGER NOT NULL REFERENCES vaults(id),
            file_path    TEXT NOT NULL,
            title        TEXT,
            content      TEXT,
            frontmatter  TEXT,
            content_hash TEXT NOT NULL,
            modified_at  REAL,
            word_count   INTEGER,
            chunk_index  INTEGER DEFAULT 0,
            section      TEXT,
            parent_id    INTEGER REFERENCES documents(id),
            is_moc       BOOLEAN DEFAULT FALSE,
            indexed_at   REAL DEFAULT (unixepoch()),
            UNIQUE(vault_id, file_path, chunk_index)
        );

        CREATE TABLE IF NOT EXISTS embeddings (
            id             INTEGER PRIMARY KEY,
            document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            embedding_type TEXT NOT NULL,
            model          TEXT NOT NULL,
            vector         BLOB NOT NULL,
            dimensions     INTEGER NOT NULL,
            created_at     REAL DEFAULT (unixepoch()),
            UNIQUE(document_id, embedding_type)
        );

        CREATE TABLE IF NOT EXISTS barycenters (
            document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            embedding_type TEXT NOT NULL,
            vector         BLOB NOT NULL,
            component_ids  TEXT NOT NULL,
            component_count INTEGER,
            updated_at     REAL DEFAULT (unixepoch()),
            PRIMARY KEY(document_id, embedding_type)
        );

        CREATE TABLE IF NOT EXISTS watcher_state (
            vault_id    INTEGER NOT NULL REFERENCES vaults(id),
            file_path   TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            modified_at  REAL,
            PRIMARY KEY(vault_id, file_path)
        );
    """)
    conn.commit()
    return conn


def upsert_vault(conn: sqlite3.Connection, name: str, root_path: str) -> int:
    cur = conn.execute(
        "INSERT INTO vaults(name, root_path) VALUES(?, ?)"
        " ON CONFLICT(name) DO UPDATE SET root_path=excluded.root_path"
        " RETURNING id",
        (name, root_path),
    )
    row = cur.fetchone()
    conn.commit()
    return row[0]


def upsert_document(
    conn: sqlite3.Connection,
    vault_id: int,
    file_path: str,
    title: str | None,
    content: str | None,
    frontmatter_json: str | None,
    content_hash: str,
    modified_at: float | None,
    word_count: int | None,
    chunk_index: int,
    section: str | None,
    parent_id: int | None,
    is_moc: bool,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO documents(
            vault_id, file_path, title, content, frontmatter,
            content_hash, modified_at, word_count, chunk_index,
            section, parent_id, is_moc, indexed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,unixepoch())
        ON CONFLICT(vault_id, file_path, chunk_index) DO UPDATE SET
            title=excluded.title,
            content=excluded.content,
            frontmatter=excluded.frontmatter,
            content_hash=excluded.content_hash,
            modified_at=excluded.modified_at,
            word_count=excluded.word_count,
            section=excluded.section,
            parent_id=excluded.parent_id,
            is_moc=excluded.is_moc,
            indexed_at=excluded.indexed_at
        RETURNING id
        """,
        (
            vault_id, file_path, title, content, frontmatter_json,
            content_hash, modified_at, word_count, chunk_index,
            section, parent_id, int(is_moc),
        ),
    )
    row = cur.fetchone()
    conn.commit()
    return row[0]


def upsert_embedding(
    conn: sqlite3.Connection,
    document_id: int,
    embedding_type: str,
    model: str,
    vector_bytes: bytes,
    dimensions: int,
) -> None:
    conn.execute(
        """
        INSERT INTO embeddings(document_id, embedding_type, model, vector, dimensions)
        VALUES(?,?,?,?,?)
        ON CONFLICT(document_id, embedding_type) DO UPDATE SET
            model=excluded.model,
            vector=excluded.vector,
            dimensions=excluded.dimensions,
            created_at=unixepoch()
        """,
        (document_id, embedding_type, model, vector_bytes, dimensions),
    )
    conn.commit()


def get_document(
    conn: sqlite3.Connection,
    vault_id: int,
    file_path: str,
    chunk_index: int,
) -> dict | None:
    cur = conn.execute(
        "SELECT * FROM documents WHERE vault_id=? AND file_path=? AND chunk_index=?",
        (vault_id, file_path, chunk_index),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def delete_document_by_path(conn: sqlite3.Connection, vault_id: int, file_path: str) -> None:
    conn.execute(
        "DELETE FROM documents WHERE vault_id=? AND file_path=?",
        (vault_id, file_path),
    )
    conn.commit()


def upsert_watcher_state(
    conn: sqlite3.Connection,
    vault_id: int,
    file_path: str,
    content_hash: str,
    modified_at: float,
) -> None:
    conn.execute(
        """
        INSERT INTO watcher_state(vault_id, file_path, content_hash, modified_at)
        VALUES(?,?,?,?)
        ON CONFLICT(vault_id, file_path) DO UPDATE SET
            content_hash=excluded.content_hash,
            modified_at=excluded.modified_at
        """,
        (vault_id, file_path, content_hash, modified_at),
    )
    conn.commit()


def get_watcher_state(conn: sqlite3.Connection, vault_id: int) -> dict[str, dict]:
    cur = conn.execute(
        "SELECT file_path, content_hash, modified_at FROM watcher_state WHERE vault_id=?",
        (vault_id,),
    )
    return {
        row["file_path"]: {"hash": row["content_hash"], "modified_at": row["modified_at"]}
        for row in cur.fetchall()
    }


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))

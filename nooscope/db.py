from __future__ import annotations

import sqlite3
import struct
import time


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply the full schema.

    Enables WAL journal mode and foreign-key enforcement. Creates all tables
    if they do not already exist; safe to call repeatedly on an existing DB.

    Args:
        db_path: Filesystem path to the SQLite database file.

    Returns:
        An open ``sqlite3.Connection`` with ``row_factory`` set to
        ``sqlite3.Row``.
    """
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

        CREATE TABLE IF NOT EXISTS pending_captures (
            id          INTEGER PRIMARY KEY,
            content     TEXT NOT NULL,
            title       TEXT,
            tags        TEXT,                       -- JSON array of strings
            source      TEXT DEFAULT 'cli',
            metadata    TEXT,                       -- JSON blob for extra fields
            created_at  REAL DEFAULT (unixepoch()),
            status      TEXT DEFAULT 'pending'      -- 'pending' | 'flushed' | 'failed'
        );

        CREATE TABLE IF NOT EXISTS pending_log_entries (
            id          INTEGER PRIMARY KEY,
            text        TEXT NOT NULL,
            refs        TEXT,                       -- JSON array of wikilink targets
            target_date TEXT NOT NULL,              -- ISO date YYYY-MM-DD: the day this belongs to
            created_at  REAL DEFAULT (unixepoch()),
            status      TEXT DEFAULT 'pending'      -- 'pending' | 'written' | 'failed'
        );
    """)
    conn.commit()
    return conn


def upsert_vault(conn: sqlite3.Connection, name: str, root_path: str) -> int:
    """Insert or update a vault record and return its row ID.

    Args:
        conn: Open SQLite connection.
        name: Unique vault name (e.g. ``"braintree"``).
        root_path: Absolute filesystem path to the vault root.

    Returns:
        Integer primary key of the vault row.
    """
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
    """Insert or update a document (or chunk) row and return its row ID.

    On conflict (vault_id, file_path, chunk_index), updates all mutable fields
    and resets ``indexed_at`` to the current time.

    Args:
        conn: Open SQLite connection.
        vault_id: Foreign key to the vault.
        file_path: Vault-relative path (e.g. ``"Projects/Nooscope.md"``).
        title: Extracted or frontmatter-supplied title. None if unavailable.
        content: Markdown body text for this chunk. None for empty files.
        frontmatter_json: JSON-serialised YAML frontmatter dict, or None.
        content_hash: SHA-256 hex digest of the raw file bytes.
        modified_at: File mtime as a Unix timestamp, or None.
        word_count: Approximate word count for the chunk body.
        chunk_index: 0 for the parent row; 1..N for heading-split sub-chunks.
        section: ``##`` heading label for this chunk, or None for chunk 0.
        parent_id: Row ID of the chunk-0 parent, or None if this is chunk 0.
        is_moc: True if the file was detected as a Map of Content.

    Returns:
        Integer primary key of the upserted document row.
    """
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
    """Insert or replace an embedding vector for a document.

    On conflict (document_id, embedding_type), updates model, vector, dimensions,
    and resets ``created_at``.

    Args:
        conn: Open SQLite connection.
        document_id: Foreign key to the documents table.
        embedding_type: Logical name for the embedding space (e.g. ``"semantic"``).
        model: Model identifier used to produce the vector (e.g. ``"bge-m3"``).
        vector_bytes: Little-endian packed float32 bytes from ``pack_vector``.
        dimensions: Number of dimensions in the vector.
    """
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
    """Fetch a single document row by its unique (vault_id, file_path, chunk_index) key.

    Args:
        conn: Open SQLite connection with row_factory set.
        vault_id: Vault scope.
        file_path: Vault-relative path to the markdown file.
        chunk_index: 0 for the parent row; higher for sub-chunks.

    Returns:
        Row as a plain ``dict``, or ``None`` if not found.
    """
    cur = conn.execute(
        "SELECT * FROM documents WHERE vault_id=? AND file_path=? AND chunk_index=?",
        (vault_id, file_path, chunk_index),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def delete_document_by_path(conn: sqlite3.Connection, vault_id: int, file_path: str) -> None:
    """Delete all document rows (all chunks) for a given vault-relative file path.

    Cascades to embeddings and barycenters via foreign-key ON DELETE CASCADE.

    Args:
        conn: Open SQLite connection.
        vault_id: Vault scope.
        file_path: Vault-relative path of the file to remove.
    """
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
    """Record or update the last-seen hash and mtime for a file in the watcher state table.

    Args:
        conn: Open SQLite connection.
        vault_id: Vault scope.
        file_path: Vault-relative path to the file.
        content_hash: SHA-256 hex digest of the file's raw bytes.
        modified_at: File mtime as a Unix timestamp.
    """
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
    """Return all watcher state records for a vault as a lookup dict.

    Args:
        conn: Open SQLite connection with row_factory set.
        vault_id: Vault scope.

    Returns:
        Mapping of vault-relative file path → ``{"hash": str, "modified_at": float}``.
    """
    cur = conn.execute(
        "SELECT file_path, content_hash, modified_at FROM watcher_state WHERE vault_id=?",
        (vault_id,),
    )
    return {
        row["file_path"]: {"hash": row["content_hash"], "modified_at": row["modified_at"]}
        for row in cur.fetchall()
    }


def insert_pending_capture(
    conn: sqlite3.Connection,
    content: str,
    title: str | None,
    tags: list[str] | None,
    source: str,
    metadata: dict | None,
) -> int:
    """Insert a new pending capture into the queue and return its row ID.

    Args:
        conn: Open SQLite connection.
        content: Body text of the capture.
        title: Optional note title.
        tags: List of tag strings; stored as JSON. None stores NULL.
        source: Originator label.
        metadata: Optional extra fields; stored as JSON. None stores NULL.

    Returns:
        Integer primary key of the new row.
    """
    import json
    cur = conn.execute(
        """
        INSERT INTO pending_captures(content, title, tags, source, metadata)
        VALUES(?,?,?,?,?)
        RETURNING id
        """,
        (
            content,
            title,
            json.dumps(tags) if tags else None,
            source,
            json.dumps(metadata) if metadata else None,
        ),
    )
    row = cur.fetchone()
    conn.commit()
    return row[0]


def list_pending_captures(conn: sqlite3.Connection) -> list[dict]:
    """Return all pending captures ordered oldest-first.

    JSON-deserialises the ``tags`` and ``metadata`` columns before returning.

    Args:
        conn: Open SQLite connection with row_factory set.

    Returns:
        List of dicts, each representing one ``pending_captures`` row with
        ``tags`` as a list and ``metadata`` as a dict.
    """
    import json
    cur = conn.execute(
        "SELECT * FROM pending_captures WHERE status='pending' ORDER BY created_at ASC"
    )
    rows = cur.fetchall()
    results = []
    for row in rows:
        r = dict(row)
        r["tags"] = json.loads(r["tags"]) if r["tags"] else []
        r["metadata"] = json.loads(r["metadata"]) if r["metadata"] else {}
        results.append(r)
    return results


def mark_capture_status(conn: sqlite3.Connection, capture_id: int, status: str) -> None:
    """Update the status of a pending capture row.

    Args:
        conn: Open SQLite connection.
        capture_id: Primary key of the capture to update.
        status: New status string (``"flushed"`` or ``"failed"``).
    """
    conn.execute(
        "UPDATE pending_captures SET status=? WHERE id=?",
        (status, capture_id),
    )
    conn.commit()


def insert_pending_log_entry(
    conn: sqlite3.Connection,
    text: str,
    refs: list[str] | None,
    target_date: str,
) -> int:
    """Insert a pending log entry for a specific daily note date and return its row ID.

    Args:
        conn: Open SQLite connection.
        text: Log entry body text.
        refs: List of wikilink target names; stored as JSON.
        target_date: ISO 8601 date string (``"YYYY-MM-DD"``) for the target daily note.

    Returns:
        Integer primary key of the new row.
    """
    import json
    cur = conn.execute(
        "INSERT INTO pending_log_entries(text, refs, target_date) VALUES(?,?,?) RETURNING id",
        (text, json.dumps(refs or []), target_date),
    )
    row = cur.fetchone()
    conn.commit()
    return row[0]


def list_pending_log_entries(conn: sqlite3.Connection) -> list[dict]:
    """Return all pending log entries ordered by target date then creation time.

    JSON-deserialises the ``refs`` column before returning.

    Args:
        conn: Open SQLite connection with row_factory set.

    Returns:
        List of dicts, each representing one ``pending_log_entries`` row with
        ``refs`` as a list of strings.
    """
    import json
    cur = conn.execute(
        "SELECT * FROM pending_log_entries WHERE status='pending' ORDER BY target_date ASC, created_at ASC"
    )
    rows = cur.fetchall()
    results = []
    for row in rows:
        r = dict(row)
        r["refs"] = json.loads(r["refs"]) if r["refs"] else []
        results.append(r)
    return results


def mark_log_entry_status(conn: sqlite3.Connection, entry_id: int, status: str) -> None:
    """Update the status of a pending log entry.

    Args:
        conn: Open SQLite connection.
        entry_id: Primary key of the log entry to update.
        status: New status string (``"written"`` or ``"failed"``).
    """
    conn.execute("UPDATE pending_log_entries SET status=? WHERE id=?", (status, entry_id))
    conn.commit()


def pack_vector(vec: list[float]) -> bytes:
    """Serialise a float vector to little-endian packed float32 bytes.

    Args:
        vec: List of floating-point values to serialise.

    Returns:
        Raw bytes suitable for storing in a SQLite BLOB column.
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Deserialise little-endian packed float32 bytes back to a Python list.

    Args:
        blob: Raw bytes produced by ``pack_vector``.

    Returns:
        List of float values with length ``len(blob) // 4``.
    """
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))

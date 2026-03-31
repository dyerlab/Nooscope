from __future__ import annotations

import sqlite3


def vault_stats(conn: sqlite3.Connection, vault_id: int | None = None) -> dict:
    """Return summary statistics about the vault index.

    Args:
        conn: Open SQLite connection with row_factory set.
        vault_id: Restrict counts to this vault. None aggregates all vaults.

    Returns:
        Dict with keys ``note_count`` (distinct files tracked), ``indexed_count``
        (files with at least one embedding), ``pending_count`` (unembedded files),
        ``embedding_types`` (list of distinct type names), and ``last_indexed``
        (Unix timestamp of the most recent indexing run, or None).
    """
    where = "WHERE vault_id=?" if vault_id else ""
    params = [vault_id] if vault_id else []

    note_count = conn.execute(
        f"SELECT COUNT(DISTINCT file_path) FROM documents {where}", params
    ).fetchone()[0]

    indexed_count = conn.execute(
        f"""
        SELECT COUNT(DISTINCT d.file_path) FROM documents d
        JOIN embeddings e ON e.document_id = d.id
        {where.replace('WHERE', 'WHERE d.')}
        """,
        params,
    ).fetchone()[0]

    embedding_types = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT embedding_type FROM embeddings"
        ).fetchall()
    ]

    last_indexed = conn.execute(
        f"SELECT MAX(indexed_at) FROM documents {where}", params
    ).fetchone()[0]

    return {
        "note_count": note_count,
        "indexed_count": indexed_count,
        "pending_count": note_count - indexed_count,
        "embedding_types": embedding_types,
        "last_indexed": last_indexed,
    }


def find_outliers(
    conn: sqlite3.Connection,
    embedding_type: str = "semantic",
    vault_id: int | None = None,
    n: int = 10,
) -> list[dict]:
    raise NotImplementedError("find_outliers is planned for Phase 5")


def temporal_drift(
    conn: sqlite3.Connection,
    embedding_type: str = "semantic",
    date_field: str = "date",
    vault_id: int | None = None,
) -> list[dict]:
    raise NotImplementedError("temporal_drift is planned for Phase 5")


def novelty_score(
    conn: sqlite3.Connection,
    file_path: str,
    embedding_type: str = "semantic",
    vault_id: int | None = None,
) -> dict:
    raise NotImplementedError("novelty_score is planned for Phase 5")

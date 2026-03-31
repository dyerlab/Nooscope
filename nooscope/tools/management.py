from __future__ import annotations

import sqlite3

from nooscope.backends.base import EmbeddingBackend
from nooscope.indexer import rebuild_vault


def rebuild_tool(
    conn: sqlite3.Connection,
    vault_id: int,
    vault_root: str,
    backends: dict[str, EmbeddingBackend],
    config,
    embedding_type: str | None = None,
) -> dict:
    """Run a full vault reindex, optionally restricted to one embedding type.

    A thin wrapper around ``rebuild_vault`` that filters the backends dict
    when a specific embedding type is requested.

    Args:
        conn: Open SQLite connection.
        vault_id: Vault to reindex.
        vault_root: Absolute path to the vault root on disk.
        backends: Mapping of embedding-type name → backend instance.
        config: Loaded ``Config`` object.
        embedding_type: If provided, only this embedding type is recomputed.
            None processes all configured backends.

    Returns:
        Dict with keys ``reindexed`` (int), ``skipped`` (int), and ``errors``
        (list of ``{"file": str, "error": str}`` dicts).
    """
    if embedding_type is not None:
        filtered = {k: v for k, v in backends.items() if k == embedding_type}
    else:
        filtered = backends

    return rebuild_vault(conn, vault_id, vault_root, filtered, config)

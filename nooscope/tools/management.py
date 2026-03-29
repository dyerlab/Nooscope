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
    if embedding_type is not None:
        filtered = {k: v for k, v in backends.items() if k == embedding_type}
    else:
        filtered = backends

    return rebuild_vault(conn, vault_id, vault_root, filtered, config)

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from nooscope.db import unpack_vector


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def search(
    conn: sqlite3.Connection,
    backends: dict,
    query: str,
    embedding_type: str = "semantic",
    vault_id: int | None = None,
    limit: int = 10,
    threshold: float = 0.6,
) -> list[dict]:
    """Embed a query and return the most similar documents from the index.

    Computes cosine similarity between the query vector and every stored
    embedding of the specified type. Results below the threshold are discarded.

    Args:
        conn: Open SQLite connection with row_factory set.
        backends: Mapping of embedding-type name → backend instance.
        query: Natural-language search string.
        embedding_type: Embedding space to search (e.g. ``"semantic"``).
        vault_id: Restrict results to this vault. None searches all vaults.
        limit: Maximum number of results after sorting by similarity.
        threshold: Minimum cosine similarity score; lower scores are excluded.

    Returns:
        List of result dicts ordered by descending similarity, each containing
        ``file_path``, ``folder``, ``title``, ``section``, ``chunk_index``,
        ``similarity``, ``content``, and ``source``.

    Raises:
        ValueError: If no backend is registered for ``embedding_type``.
    """
    backend = backends.get(embedding_type)
    if backend is None:
        raise ValueError(f"No backend for embedding_type '{embedding_type}'")

    query_vec = backend.embed([query])[0]

    where = "e.embedding_type=?"
    params: list = [embedding_type]
    if vault_id is not None:
        where += " AND d.vault_id=?"
        params.append(vault_id)

    cur = conn.execute(
        f"""
        SELECT d.id, d.file_path, d.title, d.section, d.content,
               d.chunk_index, e.vector
        FROM documents d
        JOIN embeddings e ON e.document_id = d.id
        WHERE {where}
        """,
        params,
    )
    rows = cur.fetchall()

    results = []
    for row in rows:
        vec = unpack_vector(row["vector"])
        similarity = _cosine(query_vec, vec)
        if similarity >= threshold:
            file_path = row["file_path"]
            folder = str(Path(file_path).parent) if "/" in file_path else ""
            results.append(
                {
                    "file_path": file_path,
                    "folder": folder,
                    "title": row["title"],
                    "section": row["section"],
                    "chunk_index": row["chunk_index"],
                    "similarity": similarity,
                    "content": row["content"] or "",
                    "source": "obsidian",
                }
            )

    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:limit]


def cross_space_search(
    conn: sqlite3.Connection,
    backends: dict,
    query: str,
    high_in: str = "semantic",
    low_in: str = "fdl",
    vault_id: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """Find documents that score high in one embedding space but low in another.

    Useful for surface notes that are semantically relevant but structurally
    unusual (e.g. highly semantic but low-frequency-dependent), or vice versa.
    Results are ranked by descending delta (score_high − score_low).

    Args:
        conn: Open SQLite connection with row_factory set.
        backends: Mapping of embedding-type name → backend instance.
        query: Natural-language search string embedded in both spaces.
        high_in: Embedding type expected to score high (e.g. ``"semantic"``).
        low_in: Embedding type expected to score low (e.g. ``"fdl"``).
        vault_id: Restrict results to this vault. None searches all vaults.
        limit: Maximum number of results after ranking.

    Returns:
        List of dicts ordered by descending ``delta``, each containing
        ``file_path``, ``folder``, ``title``, ``score_high``, ``score_low``,
        ``delta``, ``content``, and ``source``.

    Raises:
        ValueError: If either ``high_in`` or ``low_in`` backend is not registered.
    """
    high_backend = backends.get(high_in)
    low_backend = backends.get(low_in)
    if high_backend is None or low_backend is None:
        raise ValueError(f"Backends required for '{high_in}' and '{low_in}'")

    high_vec = high_backend.embed([query])[0]
    low_vec = low_backend.embed([query])[0]

    where = "d.chunk_index=0"
    params: list = []
    if vault_id is not None:
        where += " AND d.vault_id=?"
        params.append(vault_id)

    cur = conn.execute(
        f"""
        SELECT d.id, d.file_path, d.title, d.content,
               eh.vector AS high_vector, el.vector AS low_vector
        FROM documents d
        JOIN embeddings eh ON eh.document_id = d.id AND eh.embedding_type=?
        JOIN embeddings el ON el.document_id = d.id AND el.embedding_type=?
        WHERE {where}
        """,
        [high_in, low_in] + params,
    )
    rows = cur.fetchall()

    results = []
    for row in rows:
        score_high = _cosine(high_vec, unpack_vector(row["high_vector"]))
        score_low = _cosine(low_vec, unpack_vector(row["low_vector"]))
        delta = score_high - score_low
        file_path = row["file_path"]
        folder = str(Path(file_path).parent) if "/" in file_path else ""
        results.append(
            {
                "file_path": file_path,
                "folder": folder,
                "title": row["title"],
                "score_high": score_high,
                "score_low": score_low,
                "delta": delta,
                "content": row["content"] or "",
                "source": "obsidian",
            }
        )

    results.sort(key=lambda r: r["delta"], reverse=True)
    return results[:limit]

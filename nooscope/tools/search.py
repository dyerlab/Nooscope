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

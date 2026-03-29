from __future__ import annotations

import json
import struct
import time

import numpy as np

from nooscope.db import pack_vector, unpack_vector


def compute_barycenter(
    vectors: list[list[float]],
    weights: list[float] | None = None,
) -> list[float]:
    if not vectors:
        raise ValueError("No vectors provided")
    arr = np.array(vectors, dtype=np.float32)
    if weights is None:
        result = arr.mean(axis=0)
    else:
        w = np.array(weights, dtype=np.float32)
        w = w / w.sum()
        result = (arr * w[:, None]).sum(axis=0)
    return result.tolist()


def update_moc_barycenter(
    conn,
    document_id: int,
    embedding_type: str,
    vault_id: int,
) -> None:
    import re

    from nooscope.db import upsert_embedding

    transclusion_re = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

    cur = conn.execute(
        "SELECT content FROM documents WHERE id=?", (document_id,)
    )
    row = cur.fetchone()
    if not row:
        return

    content = row["content"] if hasattr(row, "__getitem__") else row[0]
    stems = transclusion_re.findall(content or "")

    component_ids = []
    vectors = []
    model = None
    dimensions = None

    for stem in stems:
        stem_clean = stem.strip()
        cur2 = conn.execute(
            """
            SELECT d.id, e.vector, e.model, e.dimensions FROM documents d
            JOIN embeddings e ON e.document_id = d.id
            WHERE d.vault_id=? AND d.chunk_index=0 AND e.embedding_type=?
              AND (d.file_path LIKE ? OR d.file_path = ?)
            LIMIT 1
            """,
            (vault_id, embedding_type, f"%{stem_clean}.md", f"{stem_clean}.md"),
        )
        r = cur2.fetchone()
        if r:
            component_ids.append(r["id"])
            vectors.append(unpack_vector(r["vector"]))
            if model is None:
                model = r["model"]
                dimensions = r["dimensions"]

    if not vectors:
        return

    bary = compute_barycenter(vectors)
    vector_bytes = pack_vector(bary)

    conn.execute(
        """
        INSERT INTO barycenters(document_id, embedding_type, vector, component_ids, component_count, updated_at)
        VALUES(?,?,?,?,?,unixepoch())
        ON CONFLICT(document_id, embedding_type) DO UPDATE SET
            vector=excluded.vector,
            component_ids=excluded.component_ids,
            component_count=excluded.component_count,
            updated_at=excluded.updated_at
        """,
        (document_id, embedding_type, vector_bytes, json.dumps(component_ids), len(component_ids)),
    )
    upsert_embedding(conn, document_id, embedding_type, model, vector_bytes, dimensions)
    conn.commit()


def update_chunk_barycenter(conn, parent_doc_id: int, embedding_type: str) -> None:
    from nooscope.db import upsert_embedding

    cur = conn.execute(
        """
        SELECT d.id, e.vector, e.model, e.dimensions FROM documents d
        JOIN embeddings e ON e.document_id = d.id
        WHERE d.parent_id=? AND e.embedding_type=? AND d.chunk_index > 0
        """,
        (parent_doc_id, embedding_type),
    )
    rows = cur.fetchall()
    if not rows:
        return

    vectors = [unpack_vector(r["vector"]) for r in rows]
    component_ids = [r["id"] for r in rows]
    model = rows[0]["model"]
    dimensions = rows[0]["dimensions"]

    bary = compute_barycenter(vectors)
    vector_bytes = pack_vector(bary)

    conn.execute(
        """
        INSERT INTO barycenters(document_id, embedding_type, vector, component_ids, component_count, updated_at)
        VALUES(?,?,?,?,?,unixepoch())
        ON CONFLICT(document_id, embedding_type) DO UPDATE SET
            vector=excluded.vector,
            component_ids=excluded.component_ids,
            component_count=excluded.component_count,
            updated_at=excluded.updated_at
        """,
        (parent_doc_id, embedding_type, vector_bytes, json.dumps(component_ids), len(component_ids)),
    )
    upsert_embedding(conn, parent_doc_id, embedding_type, model, vector_bytes, dimensions)
    conn.commit()

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
    """Compute the (optionally weighted) centroid of a list of vectors.

    Args:
        vectors: Non-empty list of embedding vectors; all must have the same length.
        weights: Per-vector weights. None means uniform average. Weights are
            normalised to sum to 1 before use.

    Returns:
        A new vector of the same dimension as the inputs.

    Raises:
        ValueError: If vectors is empty.
    """
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
    """Compute and store the barycenter embedding for a MOC document.

    Parses transclusion links (``![[Note]]``) from the document's content,
    fetches each referenced note's chunk-0 embedding, and replaces the MOC's
    own embedding with their centroid. The result is written to both
    ``barycenters`` and ``embeddings`` so uniform search works.

    Args:
        conn: Open SQLite connection with row_factory set.
        document_id: Primary key of the MOC document (chunk_index=0).
        embedding_type: Which embedding space to operate in (e.g. ``"semantic"``).
        vault_id: Vault scope used to resolve transclusion targets.
    """
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
    """Compute and store the barycenter of all non-zero chunk embeddings for a document.

    Fetches embeddings for all child chunks (chunk_index > 0) of parent_doc_id
    and stores their centroid as the parent's embedding. No-op if no child
    chunk embeddings exist yet.

    Args:
        conn: Open SQLite connection with row_factory set.
        parent_doc_id: Primary key of the chunk-0 (parent) document row.
        embedding_type: Which embedding space to operate in (e.g. ``"semantic"``).
    """
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

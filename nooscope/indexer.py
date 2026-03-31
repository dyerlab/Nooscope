from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import time
from fnmatch import fnmatch
from pathlib import Path

import frontmatter

from nooscope.backends.base import EmbeddingBackend
from nooscope.db import (
    delete_document_by_path,
    get_document,
    pack_vector,
    upsert_document,
    upsert_embedding,
    upsert_watcher_state,
)

def _json_default(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


_TRANSCLUSION_RE = re.compile(r"^\s*!\[\[.+\]\]\s*$")
_HEADING2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


_HEADING_RE = re.compile(r"^#{1,6}\s")


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Return True if rel_path matches any ignore pattern.

    Patterns are matched as glob expressions (fnmatch). Bare folder names
    without wildcards are also treated as prefix matches, so
    "Resources/Templates" matches "Resources/Templates/Atomic.md".
    """
    for pattern in patterns:
        if fnmatch(rel_path, pattern):
            return True
        prefix = pattern.rstrip("/")
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return True
    return False


def _is_moc(content: str) -> bool:
    non_empty = [
        line for line in content.splitlines()
        if line.strip() and not _HEADING_RE.match(line.strip())
    ]
    if not non_empty:
        return False
    transclusion_lines = sum(1 for l in non_empty if _TRANSCLUSION_RE.match(l))
    return transclusion_lines / len(non_empty) > 0.8


def _extract_title(post: frontmatter.Post, file_path: str) -> str:
    if post.get("title"):
        return str(post["title"])
    for line in post.content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(file_path).stem


def parse_document(file_path: str, vault_root: str) -> dict:
    """Parse a markdown file and return its metadata and content fields.

    Reads the file, strips YAML frontmatter, extracts a title, computes a
    content hash, and detects whether the note is a Map of Content.

    Args:
        file_path: Absolute path to the markdown file.
        vault_root: Absolute path to the vault root, used to compute the
            vault-relative ``file_path`` stored in the result.

    Returns:
        Dict with keys: ``file_path`` (vault-relative), ``title``, ``content``,
        ``frontmatter_json``, ``frontmatter`` (dict), ``content_hash``,
        ``modified_at`` (Unix timestamp), ``word_count``, and ``is_moc``.
    """
    rel_path = os.path.relpath(file_path, vault_root)
    stat = os.stat(file_path)
    modified_at = stat.st_mtime

    with open(file_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()

    post = frontmatter.loads(raw)
    content = post.content
    content_hash = hashlib.sha256(raw.encode()).hexdigest()
    word_count = len(content.split())
    title = _extract_title(post, rel_path)
    fm_dict = dict(post.metadata)
    frontmatter_json = json.dumps(fm_dict, default=_json_default) if fm_dict else None
    is_moc = _is_moc(content)

    return {
        "file_path": rel_path,
        "title": title,
        "content": content,
        "frontmatter_json": frontmatter_json,
        "frontmatter": fm_dict,
        "content_hash": content_hash,
        "modified_at": modified_at,
        "word_count": word_count,
        "is_moc": is_moc,
    }


def chunk_document(doc: dict, max_tokens: int) -> list[dict]:
    """Split a parsed document into embeddable chunks.

    If the document fits within ``max_tokens`` words, returns a single
    chunk (index 0). If it exceeds the limit and contains ``##`` headings,
    splits at each heading into chunks 1..N; the caller is responsible for
    computing a barycenter for chunk 0. Oversized documents without headings
    are returned as a single chunk (index 0) regardless of length.

    Args:
        doc: Dict produced by ``parse_document``.
        max_tokens: Approximate word-count threshold before splitting is attempted.

    Returns:
        List of chunk dicts, each with keys ``chunk_index``, ``section``,
        ``content``, ``word_count``, and ``parent_id`` (always None here;
        set by the caller after the parent row is inserted).
    """
    chunks = [
        {
            "chunk_index": 0,
            "section": None,
            "content": doc["content"],
            "word_count": doc["word_count"],
            "parent_id": None,
        }
    ]

    if doc["word_count"] <= max_tokens:
        return chunks

    parts = _HEADING2_RE.split(doc["content"])
    if len(parts) <= 1:
        return chunks

    # parts alternates: text_before_first_heading, heading1, content1, heading2, content2, ...
    idx = 1
    preamble = parts[0].strip()
    heading_chunks = []
    i = 1
    while i < len(parts) - 1:
        heading = parts[i].strip()
        body = parts[i + 1].strip()
        if body:
            heading_chunks.append((heading, body))
        i += 2

    for chunk_idx, (heading, body) in enumerate(heading_chunks, start=1):
        chunks.append(
            {
                "chunk_index": chunk_idx,
                "section": heading,
                "content": body,
                "word_count": len(body.split()),
                "parent_id": None,
            }
        )

    return chunks


def index_file(
    conn,
    vault_id: int,
    file_path: str,
    vault_root: str,
    backends: dict[str, EmbeddingBackend],
    config,
    defer_moc: bool = False,
) -> None:
    """Parse, chunk, embed, and store a single markdown file.

    Three embedding strategies are used depending on the document:
    - MOC notes: barycenter of referenced-file embeddings (or direct if ``defer_moc``).
    - Chunked notes: embed each ``##`` section separately; derive the parent vector
      as the barycenter of the section embeddings.
    - Short notes: embed the full document directly.

    Also updates the watcher state record for incremental change detection.

    Args:
        conn: Open SQLite connection.
        vault_id: Vault scope for all DB writes.
        file_path: Absolute path to the markdown file.
        vault_root: Absolute path to the vault root.
        backends: Mapping of embedding-type name → backend instance.
        config: Loaded ``Config`` object.
        defer_moc: If True, skip barycenter computation for MOC notes; used
            when calling from the first pass of ``rebuild_vault``.
    """
    from nooscope.barycenter import update_chunk_barycenter, update_moc_barycenter

    doc = parse_document(file_path, vault_root)
    chunks = chunk_document(doc, config.chunking.max_tokens)
    is_chunked = len(chunks) > 1

    # Always upsert the parent row (chunk_index=0) first to obtain its ID.
    parent_doc_id = upsert_document(
        conn,
        vault_id=vault_id,
        file_path=doc["file_path"],
        title=doc["title"],
        content=chunks[0]["content"],
        frontmatter_json=doc["frontmatter_json"],
        content_hash=doc["content_hash"],
        modified_at=doc["modified_at"],
        word_count=chunks[0]["word_count"],
        chunk_index=0,
        section=None,
        parent_id=None,
        is_moc=doc["is_moc"],
    )

    if doc["is_moc"] and not defer_moc:
        # Embed directly as a fallback for hybrid MOC notes that have prose,
        # then overwrite with barycenter once referenced files are confirmed indexed.
        if chunks[0]["content"].strip():
            for etype, backend in backends.items():
                vectors = backend.embed([chunks[0]["content"]])
                upsert_embedding(conn, parent_doc_id, etype, backend.model,
                                 pack_vector(vectors[0]), backend.dimensions)
        for etype in backends:
            update_moc_barycenter(conn, parent_doc_id, etype, vault_id)
    elif is_chunked:
        # Embed each section chunk; derive the parent's vector as their barycenter.
        for chunk in chunks[1:]:
            chunk_doc_id = upsert_document(
                conn,
                vault_id=vault_id,
                file_path=doc["file_path"],
                title=doc["title"],
                content=chunk["content"],
                frontmatter_json=doc["frontmatter_json"],
                content_hash=doc["content_hash"],
                modified_at=doc["modified_at"],
                word_count=chunk["word_count"],
                chunk_index=chunk["chunk_index"],
                section=chunk["section"],
                parent_id=parent_doc_id,
                is_moc=False,
            )
            for etype, backend in backends.items():
                if not chunk["content"].strip():
                    continue
                vectors = backend.embed([chunk["content"]])
                upsert_embedding(conn, chunk_doc_id, etype, backend.model,
                                 pack_vector(vectors[0]), backend.dimensions)
        for etype in backends:
            update_chunk_barycenter(conn, parent_doc_id, etype)
    else:
        # Fits within context window — embed the full document directly.
        if chunks[0]["content"].strip():
            for etype, backend in backends.items():
                vectors = backend.embed([chunks[0]["content"]])
                upsert_embedding(conn, parent_doc_id, etype, backend.model,
                                 pack_vector(vectors[0]), backend.dimensions)

    upsert_watcher_state(
        conn,
        vault_id=vault_id,
        file_path=doc["file_path"],
        content_hash=doc["content_hash"],
        modified_at=doc["modified_at"],
    )


def rebuild_vault(conn, vault_id: int, vault_root: str, backends: dict[str, EmbeddingBackend], config) -> dict:
    """Perform a full two-pass reindex of the vault and prune deleted files.

    First pass indexes all non-MOC files so their embeddings exist. Second
    pass indexes MOC files so their barycenters can reference the first-pass
    results. After both passes, any DB-tracked file that no longer exists on
    disk is deleted from the index.

    Args:
        conn: Open SQLite connection.
        vault_id: Vault scope.
        vault_root: Absolute path to the vault root.
        backends: Mapping of embedding-type name → backend instance.
        config: Loaded ``Config`` object; supplies ignore patterns and chunking settings.

    Returns:
        Dict with keys ``reindexed`` (int), ``skipped`` (int), and ``errors``
        (list of ``{"file": str, "error": str}`` dicts).
    """
    from nooscope.barycenter import update_moc_barycenter

    results = {"reindexed": 0, "skipped": 0, "errors": []}
    vault_path = Path(vault_root)

    all_files = sorted(vault_path.rglob("*.md"))
    moc_files: list[Path] = []

    ignore_patterns = getattr(
        next((v for v in config.vaults if v.path == vault_root), None),
        "ignore", []
    )

    # First pass: index all non-MOC files so their embeddings exist before
    # any MOC barycenter computation tries to reference them.
    for md_file in all_files:
        rel = str(md_file.relative_to(vault_root))
        if is_ignored(rel, ignore_patterns):
            results["skipped"] += 1
            continue
        try:
            doc = parse_document(str(md_file), vault_root)
            if doc["is_moc"]:
                moc_files.append(md_file)
                continue
            index_file(conn, vault_id, str(md_file), vault_root, backends, config)
            results["reindexed"] += 1
        except Exception as exc:
            results["errors"].append({"file": rel, "error": str(exc)})

    # Second pass: index MOC files and compute their barycenters now that all
    # referenced notes are present in the DB.
    for md_file in moc_files:
        rel = str(md_file.relative_to(vault_root))
        try:
            index_file(conn, vault_id, str(md_file), vault_root, backends, config,
                       defer_moc=False)
            results["reindexed"] += 1
        except Exception as exc:
            results["errors"].append({"file": rel, "error": str(exc)})

    # Prune documents for files that no longer exist on disk.
    rows = conn.execute(
        "SELECT DISTINCT file_path FROM documents WHERE vault_id=?", (vault_id,)
    ).fetchall()
    for (file_path,) in rows:
        if not (vault_path / file_path).exists():
            delete_document_by_path(conn, vault_id, file_path)

    return results

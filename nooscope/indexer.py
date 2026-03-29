from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import time
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
) -> None:
    doc = parse_document(file_path, vault_root)
    chunks = chunk_document(doc, config.chunking.max_tokens)

    parent_doc_id = None
    for chunk in chunks:
        doc_id = upsert_document(
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
            parent_id=parent_doc_id if chunk["chunk_index"] > 0 else None,
            is_moc=doc["is_moc"],
        )
        if chunk["chunk_index"] == 0:
            parent_doc_id = doc_id

        for etype, backend in backends.items():
            if not chunk["content"].strip():
                continue
            vectors = backend.embed([chunk["content"]])
            vector_bytes = pack_vector(vectors[0])
            upsert_embedding(
                conn,
                document_id=doc_id,
                embedding_type=etype,
                model=backend.model,
                vector_bytes=vector_bytes,
                dimensions=backend.dimensions,
            )

    upsert_watcher_state(
        conn,
        vault_id=vault_id,
        file_path=doc["file_path"],
        content_hash=doc["content_hash"],
        modified_at=doc["modified_at"],
    )


def rebuild_vault(conn, vault_id: int, vault_root: str, backends: dict[str, EmbeddingBackend], config) -> dict:
    results = {"reindexed": 0, "skipped": 0, "errors": []}
    vault_path = Path(vault_root)

    for md_file in sorted(vault_path.rglob("*.md")):
        rel = str(md_file.relative_to(vault_root))
        try:
            index_file(conn, vault_id, str(md_file), vault_root, backends, config)
            results["reindexed"] += 1
        except Exception as exc:
            results["errors"].append({"file": rel, "error": str(exc)})

    return results

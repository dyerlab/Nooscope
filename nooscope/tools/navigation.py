from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def read_note(
    conn: sqlite3.Connection,
    file_path: str,
    vault_root: str,
    vault_id: int | None = None,
) -> dict:
    abs_path = os.path.join(vault_root, file_path) if not os.path.isabs(file_path) else file_path
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except FileNotFoundError:
        raw = None

    cur = conn.execute(
        "SELECT * FROM documents WHERE file_path=? AND chunk_index=0"
        + (" AND vault_id=?" if vault_id else ""),
        ([file_path, vault_id] if vault_id else [file_path]),
    )
    row = cur.fetchone()

    backlinks = get_backlinks(conn, file_path, vault_id=vault_id)

    return {
        "file_path": file_path,
        "frontmatter": row["frontmatter"] if row else None,
        "content": row["content"] if row else raw,
        "backlinks": backlinks,
    }


def list_notes(
    conn: sqlite3.Connection,
    folder: str | None = None,
    vault_id: int | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    where_clauses = ["chunk_index=0"]
    params: list = []

    if vault_id is not None:
        where_clauses.append("vault_id=?")
        params.append(vault_id)

    if folder:
        where_clauses.append("file_path LIKE ?")
        params.append(f"{folder.rstrip('/')}/%")

    if tags:
        for tag in tags:
            where_clauses.append("frontmatter LIKE ?")
            params.append(f"%{tag}%")

    where = " AND ".join(where_clauses)
    params.append(limit)

    cur = conn.execute(
        f"SELECT file_path, title, modified_at, word_count FROM documents WHERE {where} ORDER BY modified_at DESC LIMIT ?",
        params,
    )
    return [
        {
            "file_path": r["file_path"],
            "title": r["title"],
            "modified_at": r["modified_at"],
            "word_count": r["word_count"],
        }
        for r in cur.fetchall()
    ]


def get_backlinks(
    conn: sqlite3.Connection,
    file_path: str,
    vault_id: int | None = None,
) -> list[dict]:
    stem = Path(file_path).stem

    where = "(content LIKE ? OR content LIKE ?)"
    params: list = [f"%[[{stem}]]%", f"%[[{stem}|%"]

    if vault_id is not None:
        where += " AND vault_id=?"
        params.append(vault_id)

    where += " AND chunk_index=0"

    cur = conn.execute(
        f"SELECT file_path, title, content FROM documents WHERE {where}",
        params,
    )
    results = []
    for row in cur.fetchall():
        content = row["content"] or ""
        lines = content.splitlines()
        snippet = ""
        for line in lines:
            if stem in line and "[[" in line:
                snippet = line.strip()[:200]
                break
        results.append(
            {
                "file_path": row["file_path"],
                "title": row["title"],
                "context_snippet": snippet,
            }
        )
    return results

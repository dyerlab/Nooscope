from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from nooscope.db import init_db, upsert_vault, upsert_document, upsert_embedding, pack_vector
from nooscope.tools.navigation import read_note, list_notes, get_backlinks
from nooscope.tools.analysis import vault_stats

FIXTURES = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def populated_db(test_db):
    conn = test_db
    vault_id = upsert_vault(conn, "test", str(FIXTURES))

    doc_id1 = upsert_document(
        conn,
        vault_id=vault_id,
        file_path="note1.md",
        title="Introduction to Knowledge Management",
        content="Personal knowledge management is the practice of collecting. See also [[note2]].",
        frontmatter_json=json.dumps({"tags": ["pkm"]}),
        content_hash="hash1",
        modified_at=1700000000.0,
        word_count=15,
        chunk_index=0,
        section=None,
        parent_id=None,
        is_moc=False,
    )

    doc_id2 = upsert_document(
        conn,
        vault_id=vault_id,
        file_path="note2.md",
        title="Chunking Strategies",
        content="When a note exceeds the context window it must be split. References [[note1]].",
        frontmatter_json=json.dumps({"tags": ["pkm", "chunking"]}),
        content_hash="hash2",
        modified_at=1700100000.0,
        word_count=14,
        chunk_index=0,
        section=None,
        parent_id=None,
        is_moc=False,
    )

    doc_id3 = upsert_document(
        conn,
        vault_id=vault_id,
        file_path="subfolder/deep.md",
        title="Deep Note",
        content="A note in a subfolder.",
        frontmatter_json=None,
        content_hash="hash3",
        modified_at=1700200000.0,
        word_count=5,
        chunk_index=0,
        section=None,
        parent_id=None,
        is_moc=False,
    )

    dummy_vec = [0.1] * 768
    for doc_id in [doc_id1, doc_id2, doc_id3]:
        upsert_embedding(
            conn,
            document_id=doc_id,
            embedding_type="semantic",
            model="nomic-embed-text",
            vector_bytes=pack_vector(dummy_vec),
            dimensions=768,
        )

    return conn, vault_id


def test_list_notes_all(populated_db):
    conn, vault_id = populated_db
    notes = list_notes(conn, vault_id=vault_id)
    assert len(notes) == 3
    titles = {n["title"] for n in notes}
    assert "Introduction to Knowledge Management" in titles
    assert "Chunking Strategies" in titles


def test_list_notes_folder_filter(populated_db):
    conn, vault_id = populated_db
    notes = list_notes(conn, folder="subfolder", vault_id=vault_id)
    assert len(notes) == 1
    assert notes[0]["title"] == "Deep Note"


def test_list_notes_tag_filter(populated_db):
    conn, vault_id = populated_db
    notes = list_notes(conn, tags=["chunking"], vault_id=vault_id)
    assert len(notes) == 1
    assert notes[0]["title"] == "Chunking Strategies"


def test_get_backlinks(populated_db):
    conn, vault_id = populated_db
    backlinks = get_backlinks(conn, "note1.md", vault_id=vault_id)
    assert any(b["file_path"] == "note2.md" for b in backlinks)


def test_get_backlinks_empty(populated_db):
    conn, vault_id = populated_db
    backlinks = get_backlinks(conn, "subfolder/deep.md", vault_id=vault_id)
    assert backlinks == []


def test_vault_stats(populated_db):
    conn, vault_id = populated_db
    stats = vault_stats(conn, vault_id=vault_id)
    assert stats["note_count"] == 3
    assert stats["indexed_count"] == 3
    assert stats["pending_count"] == 0
    assert "semantic" in stats["embedding_types"]


def test_read_note_returns_structure(populated_db):
    conn, vault_id = populated_db
    result = read_note(conn, "note1.md", str(FIXTURES), vault_id=vault_id)
    assert "file_path" in result
    assert "content" in result
    assert "backlinks" in result
    assert isinstance(result["backlinks"], list)

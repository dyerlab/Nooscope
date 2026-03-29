from __future__ import annotations

import pytest

from nooscope.indexer import chunk_document


def _make_doc(content: str, word_count: int | None = None) -> dict:
    wc = word_count if word_count is not None else len(content.split())
    return {
        "content": content,
        "word_count": wc,
        "file_path": "test.md",
        "title": "Test",
        "frontmatter_json": None,
        "frontmatter": {},
        "content_hash": "abc",
        "modified_at": 0.0,
        "is_moc": False,
    }


SHORT = "This is a short note with few words."
LONG_WITH_HEADINGS = """# Main Title

Introduction paragraph with some text.

## Section One

Content for section one goes here with multiple words to exceed limit.

## Section Two

Content for section two goes here with multiple words to exceed limit.

## Section Three

Content for section three with even more text here.
"""


def test_short_doc_single_chunk():
    doc = _make_doc(SHORT)
    chunks = chunk_document(doc, max_tokens=512)
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["section"] is None


def test_long_doc_no_headings_returns_single_chunk():
    content = " ".join(["word"] * 600)
    doc = _make_doc(content)
    chunks = chunk_document(doc, max_tokens=512)
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0


def test_long_doc_with_headings_splits():
    doc = _make_doc(LONG_WITH_HEADINGS, word_count=600)
    chunks = chunk_document(doc, max_tokens=10)
    assert len(chunks) >= 2
    chunk_0 = chunks[0]
    assert chunk_0["chunk_index"] == 0
    assert chunk_0["content"] == LONG_WITH_HEADINGS


def test_heading_chunks_have_sections():
    doc = _make_doc(LONG_WITH_HEADINGS, word_count=600)
    chunks = chunk_document(doc, max_tokens=10)
    section_names = [c["section"] for c in chunks if c["section"]]
    assert "Section One" in section_names
    assert "Section Two" in section_names
    assert "Section Three" in section_names


def test_chunk_word_counts_are_set():
    doc = _make_doc(LONG_WITH_HEADINGS, word_count=600)
    chunks = chunk_document(doc, max_tokens=10)
    for chunk in chunks:
        assert isinstance(chunk["word_count"], int)
        assert chunk["word_count"] >= 0


def test_chunk_indices_are_correct():
    doc = _make_doc(LONG_WITH_HEADINGS, word_count=600)
    chunks = chunk_document(doc, max_tokens=10)
    for i, chunk in enumerate(chunks):
        assert chunk["chunk_index"] == i


def test_parent_id_placeholder():
    doc = _make_doc(LONG_WITH_HEADINGS, word_count=600)
    chunks = chunk_document(doc, max_tokens=10)
    assert chunks[0]["parent_id"] is None
    for chunk in chunks[1:]:
        assert chunk["parent_id"] is None

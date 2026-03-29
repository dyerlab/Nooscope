from __future__ import annotations

from pathlib import Path

import pytest

from nooscope.indexer import parse_document, chunk_document

FIXTURES = Path(__file__).parent / "fixtures" / "vault"


def test_parse_document_note1():
    doc = parse_document(str(FIXTURES / "note1.md"), str(FIXTURES))
    assert doc["file_path"] == "note1.md"
    assert doc["title"] == "Introduction to Knowledge Management"
    assert "pkm" in doc["frontmatter"].get("tags", [])
    assert doc["content_hash"]
    assert doc["word_count"] > 0
    assert not doc["is_moc"]


def test_parse_document_uses_frontmatter_title():
    doc = parse_document(str(FIXTURES / "note1.md"), str(FIXTURES))
    assert doc["title"] == "Introduction to Knowledge Management"


def test_parse_document_moc_detection():
    doc = parse_document(str(FIXTURES / "moc.md"), str(FIXTURES))
    assert doc["is_moc"] is True


def test_parse_document_not_moc():
    doc = parse_document(str(FIXTURES / "note1.md"), str(FIXTURES))
    assert doc["is_moc"] is False


def test_parse_document_note2_not_moc():
    doc = parse_document(str(FIXTURES / "note2.md"), str(FIXTURES))
    assert doc["is_moc"] is False


def test_chunk_document_short_note():
    doc = parse_document(str(FIXTURES / "note1.md"), str(FIXTURES))
    chunks = chunk_document(doc, max_tokens=512)
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["section"] is None


def test_chunk_document_long_note_with_headings():
    doc = parse_document(str(FIXTURES / "note2.md"), str(FIXTURES))
    chunks = chunk_document(doc, max_tokens=10)
    assert len(chunks) > 1
    assert chunks[0]["chunk_index"] == 0
    sections = [c["section"] for c in chunks[1:]]
    assert "Heading-Based Chunking" in sections
    assert "Semantic Chunking" in sections
    assert "Fixed-Size Chunking" in sections


def test_chunk_indices_are_sequential():
    doc = parse_document(str(FIXTURES / "note2.md"), str(FIXTURES))
    chunks = chunk_document(doc, max_tokens=10)
    for i, chunk in enumerate(chunks):
        assert chunk["chunk_index"] == i


def test_chunk_content_not_empty():
    doc = parse_document(str(FIXTURES / "note2.md"), str(FIXTURES))
    chunks = chunk_document(doc, max_tokens=10)
    for chunk in chunks:
        assert chunk["content"].strip()

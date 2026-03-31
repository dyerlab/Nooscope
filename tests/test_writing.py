"""Tests for the write_note tool and _write_vault_file primitive."""
from __future__ import annotations

import pytest
from pathlib import Path

from nooscope.tools.writing import _write_vault_file, write_note


def test_write_creates_new_file(tmp_path):
    result = _write_vault_file(str(tmp_path), "Notes/Test.md", "# Hello")
    assert result["action"] == "created"
    assert result["path"] == "Notes/Test.md"
    assert (tmp_path / "Notes" / "Test.md").read_text() == "# Hello"


def test_write_updates_existing_file(tmp_path):
    (tmp_path / "Note.md").write_text("old content")
    result = _write_vault_file(str(tmp_path), "Note.md", "new content")
    assert result["action"] == "updated"
    assert (tmp_path / "Note.md").read_text() == "new content"


def test_write_creates_parent_directories(tmp_path):
    _write_vault_file(str(tmp_path), "Resources/Agents/Skills/commit.md", "content")
    assert (tmp_path / "Resources" / "Agents" / "Skills" / "commit.md").exists()


def test_write_returns_size(tmp_path):
    content = "# Hello\n\nWorld"
    result = _write_vault_file(str(tmp_path), "Test.md", content)
    assert result["size"] == len(content)


def test_write_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="escapes vault root"):
        _write_vault_file(str(tmp_path), "../outside.md", "bad")


def test_write_note_delegates_to_primitive(tmp_path):
    result = write_note(str(tmp_path), "Skills/test.md", "# Skill")
    assert result["action"] == "created"
    assert (tmp_path / "Skills" / "test.md").read_text() == "# Skill"


def test_write_note_overwrites_for_refinement(tmp_path):
    write_note(str(tmp_path), "Skills/commit.md", "# v1")
    result = write_note(str(tmp_path), "Skills/commit.md", "# v2 refined")
    assert result["action"] == "updated"
    assert (tmp_path / "Skills" / "commit.md").read_text() == "# v2 refined"


def test_flush_inbox_uses_write_primitive(tmp_path):
    """capture._flush_inbox should delegate to _write_vault_file."""
    import sqlite3
    from nooscope.db import init_db
    from nooscope.capture import queue_capture, list_pending_captures
    from nooscope.capture import _flush_inbox, _note_filename

    conn = init_db(str(tmp_path / "test.db"))
    cid = queue_capture(conn, "Test content", title="My Capture")
    from nooscope.db import list_pending_captures as _list
    capture = next(c for c in _list(conn) if c["id"] == cid)

    _flush_inbox(capture, str(tmp_path), "")
    filename = _note_filename(capture)
    assert (tmp_path / filename).exists()
    assert "Test content" in (tmp_path / filename).read_text()


def test_flush_inbox_with_subfolder(tmp_path):
    from nooscope.db import init_db, list_pending_captures
    from nooscope.capture import queue_capture, _flush_inbox, _note_filename

    conn = init_db(str(tmp_path / "test.db"))
    cid = queue_capture(conn, "Subfolder content", title="Sub")
    capture = next(c for c in list_pending_captures(conn) if c["id"] == cid)

    _flush_inbox(capture, str(tmp_path), "_inbox")
    filename = _note_filename(capture)
    assert (tmp_path / "_inbox" / filename).exists()

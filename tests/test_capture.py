from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from nooscope.db import (
    init_db,
    insert_pending_capture,
    list_pending_captures,
    mark_capture_status,
)
from datetime import date
from unittest.mock import patch

from nooscope.capture import (
    queue_capture,
    flush_captures,
    flush_log_entries,
    log_entry,
    _build_bullet,
    _append_log_bullet,
    _insert_bullet_into_lines,
    _create_from_template,
    _flush_uri,
    _slugify,
    _note_filename,
    _render_note,
)


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    return init_db(str(db))


def test_queue_capture_returns_id(conn):
    cid = queue_capture(conn, "Test thought")
    assert isinstance(cid, int)
    assert cid > 0


def test_queue_capture_appears_in_pending(conn):
    queue_capture(conn, "Alpha thought", title="Alpha", tags=["idea"], source="cli")
    pending = list_pending_captures(conn)
    assert len(pending) == 1
    assert pending[0]["content"] == "Alpha thought"
    assert pending[0]["title"] == "Alpha"
    assert pending[0]["tags"] == ["idea"]
    assert pending[0]["source"] == "cli"
    assert pending[0]["status"] == "pending"


def test_multiple_captures_ordered_by_created_at(conn):
    queue_capture(conn, "First")
    queue_capture(conn, "Second")
    pending = list_pending_captures(conn)
    assert pending[0]["content"] == "First"
    assert pending[1]["content"] == "Second"


def test_mark_flushed_removes_from_pending(conn):
    cid = queue_capture(conn, "Will be flushed")
    mark_capture_status(conn, cid, "flushed")
    pending = list_pending_captures(conn)
    assert all(c["id"] != cid for c in pending)


def test_slugify_basic():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_strips_special_chars():
    assert _slugify("What's this?!") == "whats-this"


def test_slugify_max_length():
    result = _slugify("a" * 100)
    assert len(result) <= 40


def test_note_filename_uses_title_slug(conn):
    cid = queue_capture(conn, "Some content", title="My Great Idea")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    filename = _note_filename(capture)
    assert "My Great Idea" in filename
    assert filename.endswith(".md")


def test_note_filename_date_format(conn):
    cid = queue_capture(conn, "Some content", title="Test")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    filename = _note_filename(capture)
    # Date should be YYYY.MM.DD HHMM
    import re
    assert re.match(r"\d{4}\.\d{2}\.\d{2}\.\d{4} ", filename)


def test_note_filename_falls_back_to_content(conn):
    cid = queue_capture(conn, "Fallback content here")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    filename = _note_filename(capture)
    assert "Fallback content here" in filename


def test_note_filename_strips_invalid_chars(conn):
    cid = queue_capture(conn, "body", title="My Note: A/B Test")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    filename = _note_filename(capture)
    assert "/" not in filename
    assert ":" not in filename
    assert "My Note" in filename


def test_render_note_includes_frontmatter(conn):
    cid = queue_capture(conn, "Body text", tags=["idea", "project"], source="ios")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    rendered = _render_note(capture)
    assert "---" in rendered
    assert "source: ios" in rendered
    assert "- idea" in rendered
    assert "- project" in rendered
    assert "Body text" in rendered


def test_flush_inbox_method(conn, tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    queue_capture(conn, "Inbox thought", title="Inbox Test")

    cfg = MagicMock()
    cfg.capture.flush_method = "inbox"
    cfg.capture.inbox_folder = "_inbox"
    cfg.capture.obsidian_vault_name = "TestVault"
    cfg.capture.rest_port = 27123
    cfg.capture.rest_api_key = ""
    cfg.vaults = [MagicMock(path=str(vault_root))]

    results = flush_captures(conn, cfg)
    assert results["flushed"] == 1
    assert results["failed"] == 0

    inbox = vault_root / "_inbox"
    files = list(inbox.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "Inbox thought" in content
    assert "source: cli" in content


def test_flush_marks_status_flushed(conn, tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    cid = queue_capture(conn, "Will be flushed via inbox")

    cfg = MagicMock()
    cfg.capture.flush_method = "inbox"
    cfg.capture.inbox_folder = "_inbox"
    cfg.vaults = [MagicMock(path=str(vault_root))]

    flush_captures(conn, cfg)
    pending = list_pending_captures(conn)
    assert all(c["id"] != cid for c in pending)


def test_flush_uri_missing_vault_name_fails(conn):
    queue_capture(conn, "URI thought")

    cfg = MagicMock()
    cfg.capture.flush_method = "uri"
    cfg.capture.obsidian_vault_name = ""
    cfg.vaults = [MagicMock(path="/some/path")]

    results = flush_captures(conn, cfg)
    assert results["failed"] == 1
    assert results["flushed"] == 0


def test_flush_empty_queue(conn, tmp_path):
    cfg = MagicMock()
    cfg.capture.flush_method = "inbox"
    cfg.capture.inbox_folder = "_inbox"
    cfg.vaults = [MagicMock(path=str(tmp_path))]

    results = flush_captures(conn, cfg)
    assert results["flushed"] == 0
    assert results["failed"] == 0


# --- log_entry tests ---

def _log_config(vault_root, template=False):
    cfg = MagicMock()
    cfg.capture.daily_notes_folder = "Daily"
    cfg.capture.daily_notes_format = "%Y-%m-%d"
    cfg.capture.log_section = "Notes"
    cfg.capture.obsidian_vault_name = ""
    cfg.capture.daily_notes_template = "Templates/Daily.md" if template else ""
    cfg.vaults = [MagicMock(path=str(vault_root))]
    return cfg


SAMPLE_TEMPLATE = """\
---
tags:
  - Daily
---
## Notes
-

## Tasks
-
"""


def _make_daily(tmp_path, date_str, content):
    daily_dir = tmp_path / "Daily"
    daily_dir.mkdir(exist_ok=True)
    note = daily_dir / f"{date_str}.md"
    note.write_text(content)
    return note


def test_log_entry_queues_entry(conn, tmp_path):
    cfg = _log_config(tmp_path)  # no template, no vault name → can't create note
    today = date(2024, 1, 10)
    result = log_entry(conn, str(tmp_path), "Testing", ["Nooscope"], cfg, today=today, poll=False)
    assert result["id"] > 0
    assert result["status"] == "pending"


def test_log_entry_creates_from_template(conn, tmp_path):
    cfg = _log_config(tmp_path, template=True)
    # Write the template file
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "Daily.md").write_text(SAMPLE_TEMPLATE)
    today = date(2024, 1, 10)
    result = log_entry(conn, str(tmp_path), "Template test", ["Nooscope"], cfg, today=today, poll=False)
    assert result["status"] == "written"
    note = (tmp_path / "Daily" / "2024-01-10.md").read_text()
    # Template content preserved (including <%-style tags if present)
    assert "tags:" in note
    assert "## Notes" in note
    # Logger entry inserted
    assert "logger:: Template test [[Nooscope]]" in note
    # Entry appears before ## Tasks
    assert note.index("logger::") < note.index("## Tasks")


def test_create_from_template_preserves_templater_tags(conn, tmp_path):
    cfg = _log_config(tmp_path, template=True)
    (tmp_path / "Templates").mkdir()
    template_with_tags = "---\ntags:\n  - Daily\n---\n<% tp.web.daily_quote() %>\n\n## Notes\n-\n"
    (tmp_path / "Templates" / "Daily.md").write_text(template_with_tags)
    today = date(2024, 1, 10)
    log_entry(conn, str(tmp_path), "Tag test", [], cfg, today=today, poll=False)
    note = (tmp_path / "Daily" / "2024-01-10.md").read_text()
    # Templater tags must be left untouched for Obsidian to process
    assert "<% tp.web.daily_quote() %>" in note
    assert "logger:: Tag test" in note


def test_log_entry_writes_when_note_exists(conn, tmp_path):
    cfg = _log_config(tmp_path)
    note = _make_daily(tmp_path, "2024-01-10", "---\ntags:\n  - Daily\n---\n\n## Notes\n")
    today = date(2024, 1, 10)
    result = log_entry(conn, str(tmp_path), "Testing Nooscope", ["Nooscope"], cfg, today=today, poll=False)
    assert result["status"] == "written"
    assert "logger:: Testing Nooscope [[Nooscope]]" in note.read_text()


def test_log_entry_appends_to_existing_note(conn, tmp_path):
    cfg = _log_config(tmp_path)
    note = _make_daily(tmp_path, "2024-01-10",
        "---\ntags:\n  - Daily\n---\n\n## Notes\n- logger:: First entry\n\n## Files\n")
    today = date(2024, 1, 10)
    log_entry(conn, str(tmp_path), "Second entry", [], cfg, today=today, poll=False)
    content = note.read_text()
    assert "logger:: First entry" in content
    assert "logger:: Second entry" in content
    assert content.index("Second entry") < content.index("## Files")


def test_log_entry_no_refs(conn, tmp_path):
    cfg = _log_config(tmp_path)
    _make_daily(tmp_path, "2024-01-10", "---\n---\n\n## Notes\n")
    today = date(2024, 1, 10)
    result = log_entry(conn, str(tmp_path), "Plain thought", None, cfg, today=today, poll=False)
    assert result["status"] == "written"
    assert "[[" not in result["entry"]


def test_log_entry_multiple_refs(conn, tmp_path):
    cfg = _log_config(tmp_path)
    note = _make_daily(tmp_path, "2024-01-10", "---\n---\n\n## Notes\n")
    today = date(2024, 1, 10)
    log_entry(conn, str(tmp_path), "Cross-project work", ["Nooscope", "BrainForest"], cfg, today=today, poll=False)
    assert "[[Nooscope]]" in note.read_text()
    assert "[[BrainForest]]" in note.read_text()


def test_log_entry_creates_missing_section(conn, tmp_path):
    cfg = _log_config(tmp_path)
    note = _make_daily(tmp_path, "2024-01-10", "---\ntags:\n  - Daily\n---\n\n## Tasks\n- [ ] Something\n")
    today = date(2024, 1, 10)
    log_entry(conn, str(tmp_path), "Missing section entry", [], cfg, today=today, poll=False)
    content = note.read_text()
    assert "## Notes" in content
    assert "logger:: Missing section entry" in content


def test_log_entry_uses_actual_today_by_default(conn, tmp_path):
    cfg = _log_config(tmp_path)
    fake_today = date(2025, 6, 15)
    _make_daily(tmp_path, "2025-06-15", "---\n---\n\n## Notes\n")
    with patch("nooscope.capture.date") as mock_date:
        mock_date.today.return_value = fake_today
        mock_date.fromisoformat.side_effect = date.fromisoformat
        log_entry(conn, str(tmp_path), "Dynamic date entry", [], cfg, poll=False)
    expected_file = tmp_path / "Daily" / "2025-06-15.md"
    assert "logger:: Dynamic date entry" in expected_file.read_text()


def test_flush_log_entries_retries_pending(conn, tmp_path):
    cfg = _log_config(tmp_path)
    today = date(2024, 1, 10)
    # Queue entry but note doesn't exist yet
    log_entry(conn, str(tmp_path), "Retry me", ["Nooscope"], cfg, today=today, poll=False)
    # Now create the daily note (simulating Obsidian creating it)
    note = _make_daily(tmp_path, "2024-01-10", "---\n---\n\n## Notes\n")
    # Flush should pick it up
    results = flush_log_entries(conn, str(tmp_path), cfg, poll=False)
    assert results["written"] == 1
    assert results["still_pending"] == 0
    assert "logger:: Retry me [[Nooscope]]" in note.read_text()


def test_flush_log_entries_respects_target_date(conn, tmp_path):
    cfg = _log_config(tmp_path)
    # Entry for Jan 10 queued when note didn't exist
    log_entry(conn, str(tmp_path), "Past entry", [], cfg, today=date(2024, 1, 10), poll=False)
    # Only Jan 10 note gets created — not Jan 11
    note_jan10 = _make_daily(tmp_path, "2024-01-10", "---\n---\n\n## Notes\n")
    results = flush_log_entries(conn, str(tmp_path), cfg, poll=False)
    assert results["written"] == 1
    assert "logger:: Past entry" in note_jan10.read_text()


def test_build_bullet_no_refs():
    assert _build_bullet("Hello world", None) == "- logger:: Hello world"


def test_build_bullet_with_refs():
    result = _build_bullet("Working", ["Nooscope", "Alice"])
    assert result == "- logger:: Working [[Nooscope]] [[Alice]]"


def test_render_note_strips_existing_frontmatter(conn):
    content_with_fm = "---\naliases:\n  - Foo\ntags:\n  - bar\n---\n\nActual body here."
    cid = queue_capture(conn, content_with_fm, tags=["idea"])
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    rendered = _render_note(capture)
    # Only one frontmatter block
    assert rendered.count("---") == 2
    # Our metadata present
    assert "source: cli" in rendered
    assert "- idea" in rendered
    # Content body preserved, original frontmatter gone
    assert "Actual body here." in rendered
    assert "aliases:" not in rendered


def test_render_note_plain_content_unchanged(conn):
    cid = queue_capture(conn, "No frontmatter here.")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    rendered = _render_note(capture)
    assert rendered.count("---") == 2
    assert "No frontmatter here." in rendered


def test_flush_uri_percent_encodes_spaces(conn):
    cid = queue_capture(conn, "Hello world", title="My Note")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    with patch("nooscope.capture.subprocess.run") as mock_run:
        _flush_uri(capture, "MyVault", "_inbox")
    url = mock_run.call_args[0][0][1]
    assert "+" not in url
    assert "%20" in url or "Hello%20world" in url or "My%20Note" in url


def test_flush_uri_preserves_folder_separator(conn):
    cid = queue_capture(conn, "Content", title="Note")
    pending = list_pending_captures(conn)
    capture = next(c for c in pending if c["id"] == cid)
    with patch("nooscope.capture.subprocess.run") as mock_run:
        _flush_uri(capture, "MyVault", "_inbox")
    url = mock_run.call_args[0][0][1]
    # The name parameter must contain a literal / so Obsidian creates the subfolder
    assert "name=_inbox/" in url

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from nooscope.db import (
    insert_pending_capture,
    insert_pending_log_entry,
    list_pending_captures,
    list_pending_log_entries,
    mark_capture_status,
    mark_log_entry_status,
)

log = logging.getLogger(__name__)


def queue_capture(
    conn,
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    source: str = "cli",
    metadata: dict | None = None,
) -> int:
    """Insert a new capture into the pending queue and return its row ID.

    Args:
        conn: Open SQLite connection.
        content: Body text of the note to capture.
        title: Optional note title; used for filename generation if provided.
        tags: Optional list of tag strings to attach to the note.
        source: Originator label (e.g. ``"cli"``, ``"mcp"``).
        metadata: Optional extra JSON-serialisable fields.

    Returns:
        The integer primary key of the newly inserted pending capture.
    """
    return insert_pending_capture(conn, content, title, tags or [], source, metadata or {})


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len]


def _clean_title(text: str, max_len: int = 60) -> str:
    """Sanitize text for use as a macOS filename, preserving spaces and case.

    Strips characters invalid on macOS (forward slash, colon, null byte) and
    collapses runs of whitespace. Does not lower-case or replace spaces.

    Args:
        text: Raw title or content snippet.
        max_len: Maximum character length of the returned string.

    Returns:
        A filename-safe string with spaces preserved, truncated to max_len.
    """
    text = re.sub(r"[/:\x00]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len].strip()


def _note_filename(capture: dict) -> str:
    ts = datetime.fromtimestamp(capture["created_at"], tz=timezone.utc)
    date_str = ts.strftime("%Y.%m.%d.%H%M")
    title = _clean_title(capture["title"] or capture["content"])
    return f"{date_str} {title}.md"


def _render_note(capture: dict) -> str:
    ts = datetime.fromtimestamp(capture["created_at"], tz=timezone.utc)
    tags = capture.get("tags") or []
    lines = ["---", f"date: {ts.strftime('%Y-%m-%d')}", f"source: {capture['source']}"]
    if tags:
        lines.append("tags:")
        for t in tags:
            t = t.lstrip("#")
            lines.append(f"  - {t}")
    # Strip any leading frontmatter from the content to avoid double --- blocks
    # when the captured text already contains its own YAML frontmatter.
    content = re.sub(r"^---\n.*?\n---\n?", "", capture["content"], flags=re.DOTALL).lstrip("\n")
    lines += ["---", "", content]
    return "\n".join(lines)


def _flush_inbox(capture: dict, vault_root: str, inbox_folder: str) -> None:
    from nooscope.tools.writing import _write_vault_file
    filename = _note_filename(capture)
    path = f"{inbox_folder}/{filename}" if inbox_folder else filename
    _write_vault_file(vault_root, path, _render_note(capture))


def _build_bullet(text: str, refs: list[str] | None, prefix: str = "- ") -> str:
    bullet = f"{prefix}{text.strip()}"
    if refs:
        bullet += " " + " ".join(f"[[{r.strip()}]]" for r in refs)
    return bullet


def _insert_bullet_into_lines(lines: list[str], bullet: str, section_heading: str) -> list[str]:
    """Insert a bullet into the named section of a list of lines, returning the modified list."""
    section_line = next(
        (i for i, l in enumerate(lines) if l.strip() == section_heading),
        None,
    )
    if section_line is None:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"\n{section_heading}\n{bullet}\n")
    else:
        next_section = next(
            (i for i in range(section_line + 1, len(lines))
             if re.match(r"^#{1,2}\s", lines[i])),
            len(lines),
        )
        insert_at = next_section
        while insert_at > section_line + 1 and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        lines.insert(insert_at, bullet + "\n")
    return lines


def _append_log_bullet(daily_path: Path, text: str, refs: list[str] | None, cap_cfg) -> str:
    """Append a log bullet to an existing daily note file. Returns the bullet."""
    bullet = _build_bullet(text, refs, prefix=cap_cfg.log_prefix)
    lines = daily_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = _insert_bullet_into_lines(lines, bullet, f"## {cap_cfg.log_section}")
    daily_path.write_text("".join(lines), encoding="utf-8")
    return bullet


def _create_from_template(daily_path: Path, text: str, refs: list[str] | None, vault_root: str, config) -> bool:
    """Copy the daily note template to daily_path with the log bullet already inserted.

    Returns True if the template was found and written, False otherwise.
    """
    cap_cfg = config.capture
    if not cap_cfg.daily_notes_template:
        return False

    template_path = Path(vault_root) / cap_cfg.daily_notes_template
    if not template_path.exists():
        log.warning("Daily note template not found: %s", template_path)
        return False

    bullet = _build_bullet(text, refs, prefix=cap_cfg.log_prefix)
    lines = template_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = _insert_bullet_into_lines(lines, bullet, f"## {cap_cfg.log_section}")

    # Inject calendar agenda if enabled
    try:
        from nooscope.agenda_injector import inject_agenda
        target_date = date.fromisoformat(daily_path.stem) if daily_path.stem else date.today()
        lines = inject_agenda(lines, target_date, vault_root, config)
    except Exception as exc:
        log.warning("Agenda injection failed: %s", exc)

    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_text("".join(lines), encoding="utf-8")
    log.info("Created daily note from template: %s", daily_path.name)
    return True


def _try_flush_log_entry(
    conn,
    entry_id: int,
    text: str,
    refs: list[str],
    target_date_str: str,
    vault_root: str,
    config,
    poll: bool = True,
) -> dict:
    cap_cfg = config.capture
    target_date = date.fromisoformat(target_date_str)
    filename = target_date.strftime(cap_cfg.daily_notes_format) + ".md"
    daily_path = Path(vault_root) / cap_cfg.daily_notes_folder / filename

    if not daily_path.exists():
        if cap_cfg.daily_notes_template:
            if _create_from_template(daily_path, text, refs, vault_root, config):
                mark_log_entry_status(conn, entry_id, "written")
                bullet = _build_bullet(text, refs, prefix=cap_cfg.log_prefix)
                return {"id": entry_id, "status": "written", "file": str(daily_path), "entry": bullet}
        elif poll and target_date >= date.today() and config.vaults and config.vaults[0].obsidian_mode:
            # Obsidian-mode fallback: ask Obsidian to create the daily note via URI.
            from nooscope import obsidian as _obsidian
            _obsidian.wait_for_daily(target_date, daily_path, config)

    if not daily_path.exists():
        log.debug("Daily note not yet available for %s — entry #%d stays pending", target_date_str, entry_id)
        return {"id": entry_id, "status": "pending", "target_date": target_date_str}

    bullet = _append_log_bullet(daily_path, text, refs, cap_cfg)
    mark_log_entry_status(conn, entry_id, "written")
    log.info("Log entry #%d written to %s", entry_id, daily_path.name)
    return {"id": entry_id, "status": "written", "file": str(daily_path), "entry": bullet}


def log_entry(
    conn,
    vault_root: str,
    text: str,
    refs: list[str] | None,
    config,
    today: date | None = None,
    poll: bool = True,
) -> dict:
    """Queue a log entry for today's daily note and attempt immediate flush.

    Always queues first so the entry is never lost, then tries to append to the
    daily note immediately. If the note doesn't exist yet, creates it from the
    configured template. If still unavailable (and obsidian_mode is off),
    returns status='pending'; the entry will be retried by flush_log_entries()
    or automatically when the watcher sees the daily note created.
    """
    today = today or date.today()
    target_date_str = today.isoformat()
    entry_id = insert_pending_log_entry(conn, text, refs or [], target_date_str)
    return _try_flush_log_entry(conn, entry_id, text, refs or [], target_date_str, vault_root, config, poll=poll)


def flush_log_entries(conn, vault_root: str, config, poll: bool = False) -> dict:
    """Retry all pending log entries. Called by the watcher on daily note creation
    or manually via `nooscope flush-logs`."""
    pending = list_pending_log_entries(conn)
    results: dict = {"written": 0, "still_pending": 0, "errors": []}
    for entry in pending:
        try:
            result = _try_flush_log_entry(
                conn, entry["id"], entry["text"], entry["refs"],
                entry["target_date"], vault_root, config, poll=poll,
            )
            if result["status"] == "written":
                results["written"] += 1
            else:
                results["still_pending"] += 1
        except Exception as exc:
            results["errors"].append({"id": entry["id"], "error": str(exc)})
    return results


def flush_captures(conn, config) -> dict:
    """Flush all pending captures to the vault inbox via the configured method.

    Iterates every ``status='pending'`` capture and dispatches it using
    ``capture.flush_method``.  ``inbox`` is the default and only method available
    without ``obsidian_mode: true``; ``uri`` and ``rest`` require Obsidian.

    Args:
        conn: Open SQLite connection.
        config: Loaded ``Config`` object supplying ``capture`` and ``vaults`` settings.

    Returns:
        Dict with keys ``flushed`` (int), ``failed`` (int), ``errors`` (list of
        ``{"id": int, "error": str}`` dicts), and ``previews`` (list of
        ``{"id", "filename", "source"}`` dicts for every item processed).
    """
    pending = list_pending_captures(conn)
    results: dict = {"flushed": 0, "failed": 0, "errors": [], "previews": []}

    cap_cfg = config.capture
    vault_root = config.vaults[0].path if config.vaults else ""
    obsidian_mode = config.vaults[0].obsidian_mode if config.vaults else False

    for capture in pending:
        filename = _note_filename(capture)
        results["previews"].append({
            "id": capture["id"],
            "filename": filename,
            "source": capture["source"],
        })

        try:
            if cap_cfg.flush_method == "uri":
                if not obsidian_mode:
                    raise ValueError("flush_method 'uri' requires obsidian_mode: true in vault config")
                from nooscope import obsidian as _obsidian
                if not cap_cfg.obsidian_vault_name:
                    raise ValueError("capture.obsidian_vault_name must be set for uri flush method")
                _obsidian.flush_uri(capture, cap_cfg.obsidian_vault_name, cap_cfg.inbox_folder)
            elif cap_cfg.flush_method == "inbox":
                if not vault_root:
                    raise ValueError("No vault configured")
                _flush_inbox(capture, vault_root, cap_cfg.inbox_folder)
            elif cap_cfg.flush_method == "rest":
                if not obsidian_mode:
                    raise ValueError("flush_method 'rest' requires obsidian_mode: true in vault config")
                from nooscope import obsidian as _obsidian
                _obsidian.flush_rest(capture, cap_cfg.inbox_folder, cap_cfg.rest_port, cap_cfg.rest_api_key)
            else:
                raise ValueError(f"Unknown flush_method: {cap_cfg.flush_method!r}")

            mark_capture_status(conn, capture["id"], "flushed")
            results["flushed"] += 1

        except Exception as exc:
            mark_capture_status(conn, capture["id"], "failed")
            results["failed"] += 1
            results["errors"].append({"id": capture["id"], "error": str(exc)})

    return results

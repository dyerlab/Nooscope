from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.parse
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
    return insert_pending_capture(conn, content, title, tags or [], source, metadata or {})


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len]


def _note_filename(capture: dict) -> str:
    ts = datetime.fromtimestamp(capture["created_at"], tz=timezone.utc)
    date_str = ts.strftime("%Y-%m-%d-%H%M")
    slug = _slugify(capture["title"] or capture["content"])
    return f"{date_str}-{slug}.md"


def _render_note(capture: dict) -> str:
    ts = datetime.fromtimestamp(capture["created_at"], tz=timezone.utc)
    tags = capture.get("tags") or []
    lines = ["---", f"date: {ts.strftime('%Y-%m-%d')}", f"source: {capture['source']}"]
    if tags:
        lines.append("tags:")
        for t in tags:
            t = t.lstrip("#")
            lines.append(f"  - {t}")
    lines += ["---", "", capture["content"]]
    return "\n".join(lines)


def _flush_uri(capture: dict, vault_name: str, inbox_folder: str) -> None:
    filename = _note_filename(capture)
    note_path = f"{inbox_folder}/{filename[:-3]}"  # strip .md — Obsidian adds it
    content = _render_note(capture)

    # Obsidian URI has a practical content length limit (~2000 chars).
    # For longer captures, fall back to a stub note pointing the author to nooscope queue.
    if len(content) > 1800:
        content = _render_note({**capture, "content": capture["content"][:1600] + "\n\n[truncated — see nooscope queue]"})

    params = urllib.parse.urlencode({
        "vault": vault_name,
        "name": note_path,
        "content": content,
    })
    url = f"obsidian://new?{params}"
    subprocess.run(["open", url], check=True)


def _flush_inbox(capture: dict, vault_root: str, inbox_folder: str) -> None:
    inbox_path = Path(vault_root) / inbox_folder
    inbox_path.mkdir(parents=True, exist_ok=True)
    filename = _note_filename(capture)
    (inbox_path / filename).write_text(_render_note(capture), encoding="utf-8")


def _flush_rest(capture: dict, inbox_folder: str, port: int, api_key: str) -> None:
    import httpx
    filename = _note_filename(capture)
    note_path = f"{inbox_folder}/{filename}"
    url = f"http://localhost:{port}/vault/{urllib.parse.quote(note_path)}"
    headers = {"Content-Type": "text/markdown"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.put(url, content=_render_note(capture).encode(), headers=headers, timeout=10)
    resp.raise_for_status()


def _build_bullet(text: str, refs: list[str] | None) -> str:
    bullet = f"- logger:: {text.strip()}"
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
    """Append a logger:: bullet to an existing daily note file. Returns the bullet."""
    bullet = _build_bullet(text, refs)
    lines = daily_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = _insert_bullet_into_lines(lines, bullet, f"## {cap_cfg.log_section}")
    daily_path.write_text("".join(lines), encoding="utf-8")
    return bullet


def _create_from_template(daily_path: Path, text: str, refs: list[str] | None, vault_root: str, config) -> bool:
    """Copy the Templater daily note template to daily_path, with the logger bullet
    already inserted into the Notes section.

    Templater fires trigger_on_file_creation when Obsidian opens the new file
    and renders all <% %> tags in place. Plain markdown (including logger:: entries)
    survives the render untouched.

    Returns True if the template was found and written, False otherwise.
    """
    cap_cfg = config.capture
    if not cap_cfg.daily_notes_template:
        return False

    template_path = Path(vault_root) / cap_cfg.daily_notes_template
    if not template_path.exists():
        log.warning("Daily note template not found: %s", template_path)
        return False

    bullet = _build_bullet(text, refs)
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


def _open_obsidian_for_daily(target_date: date, config) -> None:
    """Fallback: fire obsidian://new so Obsidian creates the daily note from its
    folder template. Used when no daily_notes_template path is configured."""
    cap_cfg = config.capture
    if not cap_cfg.obsidian_vault_name:
        return
    note_file = f"{cap_cfg.daily_notes_folder}/{target_date.strftime(cap_cfg.daily_notes_format)}"
    params = urllib.parse.urlencode({"vault": cap_cfg.obsidian_vault_name, "file": note_file})
    url = f"obsidian://new?{params}"
    try:
        subprocess.run(["open", url], check=False)
    except FileNotFoundError:
        pass  # `open` not available (non-macOS)


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
            # Write template + entry directly. Templater processes <% %> tags
            # when Obsidian opens the file — logger:: entry survives intact.
            if _create_from_template(daily_path, text, refs, vault_root, config):
                mark_log_entry_status(conn, entry_id, "written")
                bullet = _build_bullet(text, refs)
                return {"id": entry_id, "status": "written", "file": str(daily_path), "entry": bullet}
        elif poll and target_date >= date.today() and cap_cfg.obsidian_vault_name:
            # Fallback when no template path is configured: ask Obsidian via URI
            # and poll for up to 8 seconds.
            _open_obsidian_for_daily(target_date, config)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if daily_path.exists():
                    break
                time.sleep(0.5)

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
    """Queue a logger:: entry for today's daily note and attempt immediate flush.

    Always queues first so the entry is never lost, then tries to append to the
    daily note immediately. If the note doesn't exist yet, fires obsidian://open
    and polls up to 8 seconds for Obsidian to create it. If still unavailable,
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
    pending = list_pending_captures(conn)
    results: dict = {"flushed": 0, "failed": 0, "errors": [], "previews": []}

    cap_cfg = config.capture
    vault_root = config.vaults[0].path if config.vaults else ""

    for capture in pending:
        filename = _note_filename(capture)
        results["previews"].append({
            "id": capture["id"],
            "filename": filename,
            "source": capture["source"],
        })

        try:
            if cap_cfg.flush_method == "uri":
                if not cap_cfg.obsidian_vault_name:
                    raise ValueError("capture.obsidian_vault_name must be set for uri flush method")
                _flush_uri(capture, cap_cfg.obsidian_vault_name, cap_cfg.inbox_folder)
            elif cap_cfg.flush_method == "inbox":
                if not vault_root:
                    raise ValueError("No vault configured")
                _flush_inbox(capture, vault_root, cap_cfg.inbox_folder)
            elif cap_cfg.flush_method == "rest":
                _flush_rest(capture, cap_cfg.inbox_folder, cap_cfg.rest_port, cap_cfg.rest_api_key)
            else:
                raise ValueError(f"Unknown flush_method: {cap_cfg.flush_method!r}")

            mark_capture_status(conn, capture["id"], "flushed")
            results["flushed"] += 1

        except Exception as exc:
            mark_capture_status(conn, capture["id"], "failed")
            results["failed"] += 1
            results["errors"].append({"id": capture["id"], "error": str(exc)})

    return results

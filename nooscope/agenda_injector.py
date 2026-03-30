"""Inject calendar events into the ## Agenda section of a daily note.

All-day events appear first as plain bullets; timed events follow in chronological
order. Any timed event gets a meeting note created and is linked via [[wikilink]].

When there are no events, injects a refresher bullet summarising recently modified
vault notes so the day starts with context on where things were left off.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _format_time(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%H:%M")


def _build_agenda_lines(
    events: list[dict[str, Any]],
    event_date: date,
    vault_root: str,
    config,
    dry_run: bool = False,
) -> list[str]:
    """Return markdown bullet lines for the agenda, creating meeting notes as needed."""
    from nooscope.meeting_notes import create_meeting_note, meeting_note_slug

    lines = []
    for event in events:
        title = event["title"]

        if event["is_all_day"]:
            lines.append(f"- (all day) {title}")
        else:
            time_str = _format_time(event.get("start"))
            if dry_run:
                slug = meeting_note_slug(event_date, title, event.get("organizer"))
                note_path = f"{config.calendar.meetings_folder}/{slug}"
            else:
                note_path = create_meeting_note(event, event_date, vault_root, config)
            if note_path:
                lines.append(f"- {time_str} [[{note_path}|{title}]]")
            else:
                lines.append(f"- {time_str} {title}")

    return lines


def _replace_agenda_section(lines: list[str], agenda_lines: list[str], section_heading: str) -> list[str]:
    """Replace the content of the ## Agenda section with agenda_lines.

    Finds the heading, discards existing content up to the next ## heading,
    and inserts the new lines. Preserves one blank line before the next section.
    """
    heading = f"## {section_heading}"
    heading_idx = next(
        (i for i, l in enumerate(lines) if l.strip() == heading),
        None,
    )

    if heading_idx is None:
        # Section not found — append it at the end
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"\n{heading}\n")
        for al in agenda_lines:
            lines.append(al + "\n")
        return lines

    # Find the next ## heading after the agenda heading
    next_section_idx = next(
        (i for i in range(heading_idx + 1, len(lines)) if re.match(r"^#{1,2}\s", lines[i])),
        len(lines),
    )

    # Build replacement: heading + agenda bullets + blank line before next section
    new_block: list[str] = [lines[heading_idx]]  # keep the heading line as-is
    for al in agenda_lines:
        new_block.append(al + "\n")
    new_block.append("\n")  # blank line before next section

    return lines[:heading_idx] + new_block + lines[next_section_idx:]


def _recent_notes(vault_root: str, config, days: int = 7, max_notes: int = 10) -> list[str]:
    """Return titles of recently modified notes, excluding system folders."""
    cap_cfg = config.capture
    exclude_prefixes = {
        cap_cfg.daily_notes_folder.rstrip("/"),
        "Resources/Templates",
        "_inbox",
    }
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    root = Path(vault_root)
    candidates: list[tuple[datetime, str]] = []

    for md_file in root.rglob("*.md"):
        rel = md_file.relative_to(root).as_posix()
        if any(rel.startswith(p) for p in exclude_prefixes):
            continue
        if md_file.name.startswith("."):
            continue
        mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            title = md_file.stem
            candidates.append((mtime, title))

    candidates.sort(reverse=True)
    return [title for _, title in candidates[:max_notes]]


def _generate_refresher(recent_titles: list[str]) -> str:
    """Call Claude Haiku to write a 1-2 sentence pick-up prompt from recent note titles."""
    try:
        import anthropic
    except ImportError:
        return f"Recent work: {', '.join(recent_titles[:5])}."

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return f"Recent work: {', '.join(recent_titles[:5])}."

    titles_block = "\n".join(f"- {t}" for t in recent_titles)
    prompt = (
        "You are helping someone start their day with a brief, motivating reminder of "
        "what they were recently working on. Given the list of recently modified note "
        "titles below, write 1-2 sentences in second person (\"You can pick up on...\") "
        "that highlights the most interesting threads worth continuing. Be specific and "
        "concrete — name the actual topics. Do not use bullet points or headers.\n\n"
        f"Recent notes:\n{titles_block}"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        text = re.sub(r"^#+\s+\S[^\n]*\n+", "", text).strip()
        text = re.sub(r"\s*\n\s*", " ", text)
        return text
    except Exception as exc:
        log.warning("Refresher generation failed: %s", exc)
        return f"Recent work: {', '.join(recent_titles[:5])}."


def inject_agenda(
    lines: list[str],
    target_date: date,
    vault_root: str,
    config,
) -> list[str]:
    """Fetch calendar events for target_date and inject them into the ## Agenda section.

    If calendar injection is disabled or EventKit is unavailable, returns lines unchanged.
    When there are no events, injects a refresher bullet from recently modified notes.
    """
    cal_cfg = config.calendar
    if not cal_cfg.enabled:
        return lines

    from nooscope.calendar_reader import get_events_for_date
    events = get_events_for_date(target_date, calendars=cal_cfg.calendars or None)

    if not events:
        log.info("No calendar events for %s — generating refresher", target_date)
        recent = _recent_notes(vault_root, config)
        if recent:
            refresher = _generate_refresher(recent)
            agenda_lines = [f"- No scheduled events today. {refresher}"]
        else:
            agenda_lines = ["- No scheduled events today."]
        return _replace_agenda_section(lines, agenda_lines, cal_cfg.agenda_section)

    log.info("Injecting %d calendar event(s) for %s", len(events), target_date)
    agenda_lines = _build_agenda_lines(events, target_date, vault_root, config)
    return _replace_agenda_section(lines, agenda_lines, cal_cfg.agenda_section)

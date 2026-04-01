"""Obsidian-specific write-path functions.

These functions are only active when ``vault.obsidian_mode: true`` is set in the
config.  They depend on Obsidian being installed and (for REST) the Local REST API
plugin being enabled.  Nothing in this module is imported at startup — callers
import lazily so that the default (non-Obsidian) path has zero overhead.
"""

from __future__ import annotations

import logging
import subprocess
import urllib.parse
from datetime import date

log = logging.getLogger(__name__)


def require_obsidian_mode(config) -> None:
    """Raise if obsidian_mode is not enabled for the first configured vault.

    Args:
        config: Loaded ``Config`` object.

    Raises:
        RuntimeError: If ``vault.obsidian_mode`` is not ``True``.
    """
    if not config.vaults or not config.vaults[0].obsidian_mode:
        raise RuntimeError(
            "This operation requires obsidian_mode: true in the vault config. "
            "Set it explicitly if you are using an Obsidian-managed vault."
        )


def flush_uri(capture: dict, vault_name: str, inbox_folder: str) -> None:
    """Write a pending capture to Obsidian via the obsidian://new URI scheme.

    Builds an ``obsidian://new`` URI and passes it to ``open(1)`` on macOS.
    Content is truncated to ~1800 characters because Obsidian's URI handler has
    a practical limit of around 2 000 characters.

    Args:
        capture: Pending capture dict as returned by ``list_pending_captures``.
        vault_name: Obsidian vault display name (must match exactly).
        inbox_folder: Vault-relative folder for the new note, or ``""`` for root.

    Raises:
        subprocess.CalledProcessError: If the ``open`` command fails.
    """
    from nooscope.capture import _note_filename, _render_note

    filename = _note_filename(capture)
    stem = filename[:-3]  # strip .md — Obsidian adds it
    note_path = f"{inbox_folder}/{stem}" if inbox_folder else stem
    content = _render_note(capture)

    # Obsidian URI has a practical content length limit (~2000 chars).
    if len(content) > 1800:
        content = _render_note(
            {**capture, "content": capture["content"][:1600] + "\n\n[truncated — see nooscope queue]"}
        )

    # Use percent-encoding (not form-encoding) — Obsidian's URI handler requires
    # %20 for spaces, not +. The name parameter must preserve / for folder structure.
    vault_enc = urllib.parse.quote(vault_name, safe="")
    name_enc = urllib.parse.quote(note_path, safe="/")
    content_enc = urllib.parse.quote(content, safe="")
    url = f"obsidian://new?vault={vault_enc}&name={name_enc}&content={content_enc}"
    subprocess.run(["open", url], check=True)


def flush_rest(capture: dict, inbox_folder: str, port: int, api_key: str) -> None:
    """Write a pending capture via the Obsidian Local REST API plugin.

    Requires the `Local REST API <https://github.com/coddingtonbear/obsidian-local-rest-api>`_
    plugin to be installed and enabled in Obsidian.

    Args:
        capture: Pending capture dict.
        inbox_folder: Vault-relative folder for the new note, or ``""`` for root.
        port: REST API port (default 27123).
        api_key: Bearer token for the REST API, or ``""`` if auth is disabled.

    Raises:
        httpx.HTTPStatusError: If the REST API returns a non-2xx response.
    """
    import httpx
    from nooscope.capture import _note_filename, _render_note

    filename = _note_filename(capture)
    note_path = f"{inbox_folder}/{filename}" if inbox_folder else filename
    url = f"http://localhost:{port}/vault/{urllib.parse.quote(note_path)}"
    headers = {"Content-Type": "text/markdown"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.put(url, content=_render_note(capture).encode(), headers=headers, timeout=10)
    resp.raise_for_status()


def open_for_daily(target_date: date, config) -> None:
    """Fire ``obsidian://new`` so Obsidian creates the daily note from its template.

    Used as a last-resort fallback when no ``daily_notes_template`` path is
    configured.  Does nothing if ``obsidian_vault_name`` is not set.

    Args:
        target_date: The date for which to open/create the daily note.
        config: Loaded ``Config`` object.
    """
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


def wait_for_daily(target_date: date, daily_path, config, timeout: float = 8.0) -> bool:
    """Fire ``obsidian://new`` and poll until the daily note file appears on disk.

    Args:
        target_date: The date for which to request the daily note.
        daily_path: ``pathlib.Path`` to the expected daily note file.
        config: Loaded ``Config`` object.
        timeout: Maximum seconds to wait for the file to appear.

    Returns:
        ``True`` if the file appeared within the timeout, ``False`` otherwise.
    """
    import time

    open_for_daily(target_date, config)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daily_path.exists():
            return True
        time.sleep(0.5)
    return False

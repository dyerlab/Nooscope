"""Vault write tools: general-purpose note creation and updating."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _write_vault_file(vault_root: str, path: str, content: str) -> dict:
    """Write content to a vault-relative path, creating parent directories as needed.

    This is the single write primitive used by both ``write_note`` and the
    capture flush pipeline.

    Args:
        vault_root: Absolute filesystem path to the vault root.
        path: Vault-relative path to the target file (e.g.
            ``"Resources/Agents/Skills/commit.md"``).
        content: Full file content to write.

    Returns:
        Dict with keys ``path`` (str), ``action`` (``"created"`` or
        ``"updated"``), and ``size`` (character count written).

    Raises:
        ValueError: If ``path`` resolves outside the vault root (path traversal).
    """
    abs_path = (Path(vault_root) / path).resolve()
    vault_path = Path(vault_root).resolve()
    if not str(abs_path).startswith(str(vault_path) + "/") and abs_path != vault_path:
        raise ValueError(f"Path {path!r} escapes vault root")

    existed = abs_path.exists()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")

    return {
        "path": path,
        "action": "updated" if existed else "created",
        "size": len(content),
    }


def write_note(vault_root: str, path: str, content: str) -> dict:
    """Create or overwrite a note at an explicit vault-relative path.

    Unlike ``capture_thought``, this writes immediately with no queue and
    uses the caller-supplied path verbatim. Intended for structured,
    named documents — skills, reference notes, project files — where the
    destination path is known and the content is ready to persist.

    Args:
        vault_root: Absolute filesystem path to the vault root.
        path: Vault-relative destination path including filename and ``.md``
            extension (e.g. ``"Resources/Agents/Skills/commit.md"``).
            Parent directories are created automatically.
        content: Full markdown content to write.

    Returns:
        Dict with keys ``path`` (str), ``action`` (``"created"`` or
        ``"updated"``), and ``size`` (int, character count).
    """
    return _write_vault_file(vault_root, path, content)

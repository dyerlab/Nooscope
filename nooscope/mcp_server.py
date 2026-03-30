from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from nooscope.config import load_config
from nooscope.db import init_db, upsert_vault
from nooscope.backends.ollama import OllamaBackend
from nooscope.tools.search import search as _search, cross_space_search as _cross_space_search
from nooscope.tools.navigation import read_note as _read_note, list_notes as _list_notes, get_backlinks as _get_backlinks
from nooscope.tools.analysis import vault_stats as _vault_stats
from nooscope.tools.management import rebuild_tool as _rebuild_tool
from nooscope.capture import queue_capture as _queue_capture, log_entry as _log_entry, flush_log_entries as _flush_log_entries

mcp = FastMCP("nooscope")

_state: dict = {}


def _get_state():
    if not _state:
        raise RuntimeError("MCP server not initialized — call main() first")
    return _state


@mcp.tool()
def search(
    query: str,
    embedding_type: str = "semantic",
    vault: str | None = None,
    limit: int = 10,
    threshold: float = 0.6,
) -> list[dict]:
    s = _get_state()
    vault_id = _resolve_vault_id(s, vault)
    return _search(
        s["conn"],
        s["backends"],
        query=query,
        embedding_type=embedding_type,
        vault_id=vault_id,
        limit=limit,
        threshold=threshold,
    )


@mcp.tool()
def read_note(file_path: str, vault: str | None = None) -> dict:
    s = _get_state()
    vault_id, vault_root = _resolve_vault(s, vault)
    return _read_note(s["conn"], file_path, vault_root, vault_id=vault_id)


@mcp.tool()
def list_notes(
    folder: str | None = None,
    vault: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    s = _get_state()
    vault_id = _resolve_vault_id(s, vault)
    return _list_notes(s["conn"], folder=folder, vault_id=vault_id, tags=tags, limit=limit)


@mcp.tool()
def get_backlinks(file_path: str, vault: str | None = None) -> list[dict]:
    s = _get_state()
    vault_id = _resolve_vault_id(s, vault)
    return _get_backlinks(s["conn"], file_path, vault_id=vault_id)


@mcp.tool()
def vault_stats(vault: str | None = None) -> dict:
    s = _get_state()
    vault_id = _resolve_vault_id(s, vault)
    return _vault_stats(s["conn"], vault_id=vault_id)


@mcp.tool()
def rebuild(vault: str | None = None, embedding_type: str | None = None) -> dict:
    s = _get_state()
    vault_id, vault_root = _resolve_vault(s, vault)
    return _rebuild_tool(
        s["conn"],
        vault_id=vault_id,
        vault_root=vault_root,
        backends=s["backends"],
        config=s["config"],
        embedding_type=embedding_type,
    )


@mcp.tool()
def capture_thought(
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    source: str = "mcp",
) -> dict:
    """Queue a structured note for later flush to the Obsidian vault inbox.

    Use this for content that warrants its own note: has a title, belongs in
    the permanent vault structure, and is not time-critical.
    """
    s = _get_state()
    capture_id = _queue_capture(s["conn"], content, title=title, tags=tags or [], source=source)
    return {"id": capture_id, "status": "queued"}


@mcp.tool()
def log_thought(
    text: str,
    refs: list[str] | None = None,
) -> dict:
    """Queue a logger:: entry for today's daily note.

    Use this for ephemeral, time-stamped observations — quick notes that
    reference a project, person, or meeting. The entry appears automatically
    in the Dataview log of any referenced note.

    Always queued first (never lost), then flushed immediately if the daily
    note exists. If Obsidian hasn't created today's note yet, returns
    status='pending' and retries automatically when the note appears.

    refs: list of note names to [[wikilink]] e.g. ["Nooscope", "Alice"]
    """
    s = _get_state()
    vault_root = s["config"].vaults[0].path
    return _log_entry(s["conn"], vault_root, text, refs or [], s["config"])


@mcp.tool()
def generate_vault_layout(vault: str | None = None) -> dict:
    """Scan the vault and write a layout reference document to References/VaultLayout.md.

    Creates a structured markdown document describing the vault's folder structure,
    note counts, and key locations. Safe to re-run — overwrites the existing document.
    """
    from pathlib import Path
    from collections import Counter

    s = _get_state()
    _, vault_root = _resolve_vault(s, vault)
    root = Path(vault_root)
    out_path = root / _VAULT_LAYOUT_PATH

    # Gather per-folder note counts (top-level only for the summary table)
    top_folders: dict[str, int] = {}
    all_tags: Counter = Counter()
    skip = {".obsidian", ".trash"}

    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name in skip or item.name.startswith("."):
            continue
        count = sum(1 for _ in item.rglob("*.md"))
        top_folders[item.name] = count

    # Collect tags from frontmatter across the whole vault
    import re
    tag_re = re.compile(r"^\s*-\s+(\S+)", re.MULTILINE)
    for md in root.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
            # Only look inside the YAML frontmatter
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    fm = text[3:end]
                    in_tags = False
                    for line in fm.splitlines():
                        if line.strip() == "tags:":
                            in_tags = True
                            continue
                        if in_tags:
                            if line.startswith(" ") or line.startswith("\t"):
                                m = tag_re.match(line)
                                if m:
                                    all_tags[m.group(1).lstrip("#")] += 1
                            else:
                                in_tags = False
        except OSError:
            continue

    # Build the document
    lines = [
        "---",
        "type: reference",
        "tags: [vault, navigation, structure]",
        f"created: {__import__('datetime').date.today().isoformat()}",
        "---",
        "",
        "# BrainTree Vault Layout",
        "",
        "Quick reference for vault structure, conventions, and where things live.",
        "Auto-generated by `generate_vault_layout` — edit freely, re-run to refresh structure counts.",
        "",
        "---",
        "",
        "## Top-Level Folders",
        "",
        "| Folder | Notes |",
        "|--------|-------|",
    ]
    for folder, count in sorted(top_folders.items()):
        lines.append(f"| `{folder}/` | {count} |")

    lines += [
        "",
        "---",
        "",
        "## Top Tags",
        "",
        "| Tag | Count |",
        "|-----|-------|",
    ]
    for tag, count in all_tags.most_common(20):
        lines.append(f"| `{tag}` | {count} |")

    lines += ["", "---", "", "## Key Locations", "", "*(Fill in as you discover conventions.)*", ""]

    content = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    # Reload into server instructions immediately
    mcp.instructions = content

    return {
        "status": "created",
        "path": str(out_path.relative_to(root)),
        "top_folders": top_folders,
        "top_tags": dict(all_tags.most_common(10)),
    }


def _resolve_vault_id(state: dict, vault_name: str | None) -> int | None:
    if vault_name is None:
        vaults = state["config"].vaults
        if len(vaults) == 1:
            return state["vault_ids"].get(vaults[0].name)
        return None
    return state["vault_ids"].get(vault_name)


def _resolve_vault(state: dict, vault_name: str | None) -> tuple[int | None, str]:
    config = state["config"]
    if vault_name is None:
        vc = config.vaults[0]
    else:
        vc = next((v for v in config.vaults if v.name == vault_name), config.vaults[0])
    vault_id = state["vault_ids"].get(vc.name)
    return vault_id, vc.path


def _build_backends(config) -> dict:
    backends = {}
    for etype, ecfg in config.embedding_types.items():
        if ecfg.backend == "ollama":
            backends[etype] = OllamaBackend(model=ecfg.model, dimensions=ecfg.dimensions)
        elif ecfg.backend == "openai":
            from nooscope.backends.openai import OpenAIBackend
            backends[etype] = OpenAIBackend(model=ecfg.model, dimensions=ecfg.dimensions)
        elif ecfg.backend == "mlx":
            from nooscope.backends.mlx import MLXBackend
            backends[etype] = MLXBackend(model=ecfg.model, dimensions=ecfg.dimensions)
        elif ecfg.backend == "apple_nl":
            from nooscope.backends.apple_nl import AppleNLBackend
            backends[etype] = AppleNLBackend(model=ecfg.model, dimensions=ecfg.dimensions)
        elif ecfg.backend == "fdl":
            from nooscope.backends.fdl import FDLBackend
            backends[etype] = FDLBackend(model=ecfg.model, dimensions=ecfg.dimensions)
    return backends


_VAULT_LAYOUT_PATH = "References/VaultLayout.md"
_VAULT_LAYOUT_MISSING = (
    "The vault layout reference document (References/VaultLayout.md) is missing. "
    "Offer the user to create it by calling the `generate_vault_layout` tool — "
    "it will scan the vault and write the document automatically."
)


def _load_vault_layout(config) -> str:
    """Read References/VaultLayout.md from the primary vault.

    Returns the file contents if found, or a fallback instruction prompting
    the agent to offer creation via the generate_vault_layout tool.
    """
    from pathlib import Path
    if not config.vaults:
        return _VAULT_LAYOUT_MISSING
    layout_path = Path(config.vaults[0].path) / _VAULT_LAYOUT_PATH
    try:
        return layout_path.read_text(encoding="utf-8")
    except OSError:
        return _VAULT_LAYOUT_MISSING


def main() -> None:
    config_path = os.environ.get("NOOSCOPE_CONFIG")
    config = load_config(config_path)

    backends = _build_backends(config)

    vault_ids: dict[str, int] = {}
    conn = None

    for vault_cfg in config.vaults:
        vault_conn = init_db(vault_cfg.db_path)
        if conn is None:
            conn = vault_conn
        vid = upsert_vault(vault_conn, vault_cfg.name, vault_cfg.path)
        vault_ids[vault_cfg.name] = vid

    _state.update(
        {
            "config": config,
            "conn": conn,
            "backends": backends,
            "vault_ids": vault_ids,
        }
    )

    mcp.instructions = _load_vault_layout(config)

    mcp.run()


if __name__ == "__main__":
    main()

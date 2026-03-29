# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nooscope is a Python-based MCP (Model Context Protocol) server that maintains a local, multi-embedding vector index of Obsidian markdown vaults. It exposes semantic search, corpus analysis, and capture tools to AI assistants via MCP.

**Status:** Implemented and operational. The vault at `/Volumes/Developer/BrainTree` is indexed (1,784 notes, 4,839 embeddings). The MCP server runs via `nooscope serve` and is registered with both Claude Code CLI and Claude Desktop.

The primary vault is at `/Volumes/Developer/BrainTree`. The project note is at `Projects/Nooscope.md` in that vault.

## Commands

```bash
pip install -e .                   # Install in editable mode (venv at .venv/)
pipx install .                     # Install globally via pipx (required for Claude Desktop)
pipx install --force .             # Re-sync pipx after code changes
.venv/bin/pytest                   # Run all tests
.venv/bin/pytest tests/test_capture.py  # Run a single test file

nooscope rebuild                   # Full vault reindex (~30-60 min for 1,900 notes)
nooscope watch                     # Start incremental file watcher
nooscope serve                     # Start MCP server (stdio)

nooscope log "text" --refs "Note,Person"   # Append logger:: to today's daily note
nooscope log "text" --date 2026-04-01      # Log to a specific date's daily note
nooscope capture "text" --title "..." --tags "t1,t2"  # Queue a structured note
nooscope queue                     # List pending captures
nooscope flush                     # Flush queued captures to Obsidian inbox
nooscope flush-logs                # Retry pending log entries
```

## MCP registration

**Claude Code CLI** — uses `~/.claude.json` (managed by `claude mcp` commands, not `settings.json`):

```bash
claude mcp add --scope user \
  -e NOOSCOPE_CONFIG=/Volumes/Developer/Applications/Nooscope/nooscope.yaml \
  -- nooscope /Users/rodney/.local/bin/nooscope serve

claude mcp list   # verify
```

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nooscope": {
      "command": "/Users/rodney/.local/bin/nooscope",
      "args": ["serve"],
      "env": {
        "NOOSCOPE_CONFIG": "/Volumes/Developer/Applications/Nooscope/nooscope.yaml"
      }
    }
  }
}
```

**Important:** Claude Desktop runs sandboxed and cannot access `/Volumes/`. Always use the pipx binary (`~/.local/bin/nooscope`) in the Desktop config, never the `.venv/bin/nooscope` path. After code changes, run `pipx install --force .` to sync.

## Architecture

```
Vault (markdown) → nooscope-watcher (watchdog/fsevents) → nooscope.db (SQLite)
                                                                  ↓
                                                   MCP Server (stdio)
                                                   → Claude Code / Claude Desktop
```

**Core principles:**
- Vault is canonical truth. `nooscope.db` is a rebuildable derivative.
- Nooscope is read-only with respect to the vault, with two explicit exceptions: `log` writes to the daily note's `## Notes` section; `flush` writes to `_inbox/`.

### Module responsibilities

- `nooscope/cli.py` — Entry points: `rebuild`, `watch`, `serve`, `log`, `capture`, `queue`, `flush`, `flush-logs`
- `nooscope/config.py` — Load and validate `nooscope.yaml`
- `nooscope/db.py` — SQLite schema, CRUD, vector pack/unpack
- `nooscope/indexer.py` — Parse, chunk by `##` headings, embed, store; two-pass MOC handling
- `nooscope/barycenter.py` — MOC and chunk barycenter computation; results stored in both `barycenters` and `embeddings` tables for uniform search
- `nooscope/watcher.py` — Incremental updates via watchdog; triggers `flush_log_entries` when a daily note is created
- `nooscope/capture.py` — Two capture modes: structured notes (queued → `_inbox/`) and ephemeral log entries (written to daily note, queued if note doesn't exist yet)
- `nooscope/mcp_server.py` — FastMCP server with all tools
- `nooscope/backends/` — Embedding backends implementing `EmbeddingBackend` from `base.py`
- `nooscope/tools/` — MCP tool groups: `search`, `navigation`, `analysis`, `management`

### MCP tools available

| Tool | Description |
|---|---|
| `search` | Semantic vector search, returns scored results with `file_path` |
| `cross_space_search` | Compare scores across two embedding types |
| `read_note` | Read note content + frontmatter + backlinks |
| `list_notes` | Browse by folder, tags, recency |
| `get_backlinks` | Find all notes linking to a given note |
| `vault_stats` | Index counts and status |
| `capture_thought` | Queue a structured note for flush to `_inbox/` |
| `log_thought` | Append `logger::` entry to today's daily note |
| `rebuild` | Full vault reindex |

### Capture queue design

**Structured capture** (`capture_thought` / `nooscope capture`):
- Queued in `pending_captures` table
- Flushed via `nooscope flush` → creates file in `_inbox/` folder
- Three flush methods: `uri` (Obsidian creates it), `inbox` (direct write), `rest` (Obsidian REST API plugin)

**Log entry** (`log_thought` / `nooscope log`):
- Always queued first in `pending_log_entries` with `target_date`
- Immediately appends to daily note if it exists
- If daily note is missing: writes template (`daily_notes_template` in config) with `logger::` entry already in `## Notes` section; Templater processes `<% %>` tags when Obsidian next opens the file
- `target_date` preserved so past entries always land in the correct date's note

### Chunking

- Fits in context window → embedded directly as `chunk_index=0`
- Oversized + `##` headings → split into chunks 1..N; `chunk_index=0` gets barycenter of chunk embeddings
- MOC notes (`is_moc=true`) → barycenter of `![[referenced]]` file embeddings
- Oversized + no headings → embedded as-is (will get 400 from Ollama if too large); candidates for manual refactoring

### Embedding backends

All implement `embed(texts: list[str]) -> list[list[float]]` and `is_available() -> bool`.

| Backend | Platform | Notes |
|---|---|---|
| `OllamaBackend` | Any | **Default.** Requires Ollama with `nomic-embed-text` |
| `MLXBackend` | macOS/Apple Silicon | Requires `mlx-lm` (stub) |
| `AppleNLBackend` | macOS | `NaturalLanguage.framework` via PyObjC (stub) |
| `OpenAIBackend` | Any | Requires `OPENAI_API_KEY` (stub) |
| `FDLBackend` | Any | Frequency-dependent loading (not yet implemented) |

## Configuration

`nooscope.yaml` (gitignored). See `nooscope.yaml.example` for full template including `capture:` section.

Key capture config:
```yaml
capture:
  flush_method: uri
  obsidian_vault_name: BrainTree
  daily_notes_folder: Resources/Daily
  daily_notes_format: "%Y-%m-%d"
  log_section: Notes
  daily_notes_template: "Resources/Templates/Daily Note.md"
```

## Known issues

- ~128 notes failed indexing with 400 errors (exceed nomic-embed-text context window, no `##` heading structure to chunk on). Candidates for manual refactoring.
- 3 notes have malformed YAML frontmatter (`Andy Matuschuck.md` and others).

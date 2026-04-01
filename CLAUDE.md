# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nooscope is a Python-based MCP (Model Context Protocol) server that maintains a local, multi-embedding vector index of markdown vaults. It exposes semantic search, navigation, and capture tools to AI assistants via MCP. The core pipeline is editor-agnostic: any folder of markdown files can be indexed and served.

**Status:** Implemented and operational. The active vault is the iCloud BrainTree folder (10 notes). The MCP server runs via `nooscope serve` and is registered with both Claude Code CLI and Claude Desktop. The file watcher runs automatically via LaunchAgent at login.

**Active vault:** `/Users/rodney/Library/Mobile Documents/com~apple~CloudDocs/BrainTree`
**Project note:** `Projects/Nooscope.md` in that vault (create via `project-init` skill if it doesn't exist yet)
**DB:** vault-local at `.nooscope/nooscope.db`

## Commands

```bash
pip install -e .                   # Install in editable mode (venv at .venv/)
pipx install .                     # Install globally via pipx (required for Claude Desktop)
pipx install --force .             # Re-sync pipx after code changes
.venv/bin/pytest                   # Run all tests
.venv/bin/pytest tests/test_capture.py  # Run a single test file

nooscope rebuild                   # Full vault reindex; also prunes deleted files from index
nooscope watch                     # Start incremental file watcher (normally runs via LaunchAgent)
nooscope serve                     # Start MCP server (stdio)

nooscope log "text"                # Append bullet to today's daily note
nooscope log "text" --date 2026.04.01   # Log to a specific date's daily note
nooscope capture "text" --title "..." --tags "t1,t2"  # Queue a structured note
nooscope queue                     # List pending captures
nooscope flush                     # Flush queued captures to vault inbox
nooscope flush-logs                # Retry pending log entries
nooscope inject-agenda             # Inject today's calendar events into daily note
nooscope inject-agenda --date 2026.04.01   # Inject for a specific date
nooscope inject-agenda --dry-run   # Preview without writing
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

## File watcher (LaunchAgent)

The watcher runs automatically via a LaunchAgent that starts at login:

- **Plist:** `~/Library/LaunchAgents/com.dyerlab.nooscope.watch.plist`
- **App bundle:** `~/Applications/Nooscope.app` (ad-hoc signed; makes it show as "Nooscope" in macOS Background Items instead of "python3")
- **Logs:** `~/.local/share/nooscope/watch.log`

```bash
launchctl list | grep nooscope          # verify running
launchctl unload ~/Library/LaunchAgents/com.dyerlab.nooscope.watch.plist
launchctl load  ~/Library/LaunchAgents/com.dyerlab.nooscope.watch.plist
tail -f ~/.local/share/nooscope/watch.log
```

After updating `Nooscope.app/Contents/MacOS/NooscopeWatcher` or `Info.plist`, re-sign:
```bash
codesign --sign - --force ~/Applications/Nooscope.app
```

## Architecture

```
Vault (markdown folder) → nooscope-watcher (watchdog/fsevents) → .nooscope/nooscope.db (SQLite)
                                                                            ↓
                                                             MCP Server (stdio)
                                                             → Claude Code / Claude Desktop
```

**Core principles:**
- Vault is canonical truth. `.nooscope/nooscope.db` is a rebuildable derivative.
- The pipeline is editor-agnostic. Obsidian-specific features activate only when `obsidian_mode: true` is set on the vault.
- Nooscope is read-only with respect to the vault, with explicit exceptions: `log` and `inject-agenda` write to daily notes; `flush` and `write_note` write arbitrary notes to the vault.

### Module responsibilities

- `nooscope/cli.py` — Entry points: `rebuild`, `watch`, `serve`, `log`, `capture`, `queue`, `flush`, `flush-logs`, `inject-agenda`
- `nooscope/config.py` — Load and validate `nooscope.yaml`
- `nooscope/db.py` — SQLite schema, CRUD, vector pack/unpack
- `nooscope/indexer.py` — Parse, chunk by `##` headings, embed, store; two-pass MOC handling; stale-file pruning on rebuild; `is_ignored()` for vault ignore patterns
- `nooscope/barycenter.py` — MOC and chunk barycenter computation; results stored in both `barycenters` and `embeddings` tables for uniform search
- `nooscope/watcher.py` — Incremental updates via watchdog; triggers `flush_log_entries` when a daily note is created; auto-flushes pending captures every 30 seconds; respects vault ignore patterns
- `nooscope/capture.py` — Two capture modes: structured notes (queued → vault) and ephemeral log entries (written to daily note, queued if note doesn't exist yet). Log bullet prefix is configurable via `capture.log_prefix`.
- `nooscope/obsidian.py` — **Obsidian-specific write path** (only used when `obsidian_mode: true`): `flush_uri`, `flush_rest`, `open_for_daily`, `wait_for_daily`, `require_obsidian_mode`
- `nooscope/agenda_injector.py` — Injects calendar events into the `## Agenda` section of a daily note; falls back to a Claude-generated refresher when no events exist
- `nooscope/calendar_reader.py` — EventKit bridge (via `pyobjc-framework-EventKit`) for reading macOS Calendar events without AppleScript
- `nooscope/meeting_notes.py` — Creates per-event meeting notes with Claude-generated context
- `nooscope/mcp_server.py` — FastMCP server with all tools; sets instructions via `mcp._mcp_server.instructions` (FastMCP 1.26+ has no public setter)
- `nooscope/backends/` — Embedding backends implementing `EmbeddingBackend` from `base.py`
- `nooscope/tools/` — MCP tool groups: `search`, `navigation`, `analysis`, `management`, `writing`

### MCP tools available

| Tool | Description |
|---|---|
| `search` | Semantic vector search, returns scored results with `file_path` |
| `cross_space_search` | Compare scores across two embedding types |
| `read_note` | Read note content + frontmatter + backlinks |
| `list_notes` | Browse by folder, tags, recency |
| `get_backlinks` | Find all notes linking to a given note (matches both `[[wikilinks]]` and `[markdown](relative.md)` links) |
| `vault_stats` | Index counts and status |
| `capture_thought` | Queue a structured note for automatic flush to the vault |
| `write_note` | Create or overwrite a note at an explicit vault-relative path |
| `log_thought` | Append a log bullet to today's daily note |
| `rebuild` | Full vault reindex |
| `generate_vault_layout` | Scan vault and write `References/VaultLayout.md`; also reloads MCP server instructions |

### Capture queue design

**Structured capture** (`capture_thought` / `nooscope capture`):
- Queued in `pending_captures` table
- Auto-flushed by the watcher every 30 seconds, or manually via `nooscope flush`
- Default flush method is `inbox` (direct write to disk — no external dependencies)
- `uri` and `rest` methods are available only when `obsidian_mode: true`
- Captured files land at vault root by default (`inbox_folder: ""`); set to a subfolder name to collect them elsewhere
- Filename format: `YYYY.MM.DD.HHMM Title As Written.md` — spaces preserved, macOS-safe characters only (`/` `:` stripped)

**Direct write** (`write_note`):
- Writes immediately to a caller-specified vault-relative path — no queue, no timestamp prefix
- Creates or overwrites (upsert) — designed for living documents that get refined over time
- Parent directories created automatically
- Use for: skills, project notes, reference docs — anything with a permanent, meaningful name
- Path traversal (`../`) is rejected

**Skills** live at `Resources/Skills/`. Each skill is a plain markdown file describing purpose, trigger conditions, step-by-step instructions, and output format in a model-agnostic format any LLM can read and apply. Skills are indexed by nooscope and searchable. When Claude Code is started directly in a vault folder, it reads the same skill files without any MCP indirection.

Current skills: `list-participants`, `meeting-context`, `project-init`, `project-commit-log`

**Log entry** (`log_thought` / `nooscope log`):
- Always queued first in `pending_log_entries` with `target_date`
- Immediately appends to daily note if it exists
- If daily note is missing: writes from `daily_notes_template` (plain text with `{placeholder}` substitution)
- `target_date` preserved so past entries always land in the correct date's note
- Bullet prefix is `capture.log_prefix` (default `"- "`; set to `"logger:: "` for Obsidian Dataview)

**Calendar agenda** (`nooscope inject-agenda`):
- Reads events via EventKit (`pyobjc-framework-EventKit`); requires `calendar.enabled: true` in config
- Replaces the `## Agenda` section of the daily note with today's events
- Timed events get a meeting note created in `calendar.meetings_folder` and are linked via relative markdown link
- If the daily note doesn't exist, creates it from `daily_notes_template` first
- If no events: injects a Claude Haiku-generated refresher from recently modified notes
- Requires macOS Calendar permission on first run (TCC prompt)

### Template system

Templates use plain `{placeholder}` substitution via `str.format_map()` with a `SafeDict` that leaves unknown keys untouched. No external templating dependency.

**Standard keys:**

| Key | Resolves to |
|---|---|
| `{date}` | Today's date in `daily_notes_format` (e.g. `2026.04.01`) |
| `{title}` | Note title from filename or user input |
| `{daily-note}` | Relative markdown link to today's daily note |
| `{skill:name}` | AI-generated content from `Resources/Skills/name.md` |

`{skill:name}` keys are resolved by reading the named skill file and passing it to the Claude API with available context variables. Unknown keys are left as-is in the rendered output.

### Chunking

- Fits in context window → embedded directly as `chunk_index=0`
- Oversized + `##` headings → split into chunks 1..N; `chunk_index=0` gets barycenter of chunk embeddings
- MOC notes (`is_moc=true`) → barycenter of `![[referenced]]` file embeddings (Obsidian vaults only)
- Oversized + no headings → embedded as-is; candidates for manual refactoring

### Embedding backends

All implement `embed(texts: list[str]) -> list[list[float]]` and `is_available() -> bool`.

| Backend | Platform | Notes |
|---|---|---|
| `OllamaBackend` | Any | **Default.** Requires Ollama running locally |
| `MLXBackend` | macOS/Apple Silicon | Requires `mlx-lm` (stub) |
| `AppleNLBackend` | macOS | `NaturalLanguage.framework` via PyObjC (stub) |
| `OpenAIBackend` | Any | Requires `OPENAI_API_KEY` (stub) |
| `FDLBackend` | Any | Frequency-dependent loading (not yet implemented) |

## Configuration

`nooscope.yaml` (gitignored). See `nooscope.yaml.example` for full template.

Key settings:
```yaml
vaults:
  - name: braintree
    path: /Users/rodney/Library/Mobile Documents/com~apple~CloudDocs/BrainTree
    db_path: /Users/rodney/Library/Mobile Documents/com~apple~CloudDocs/BrainTree/.nooscope/nooscope.db
    obsidian_mode: false           # true enables URI/REST flush, Templater fallback, wikilink-only backlinks
    ignore:
      - Resources/Templates        # template placeholders are not content

embeddings:
  semantic:
    backend: ollama
    model: bge-m3                  # 1024 dimensions, 8192-token context
    dimensions: 1024

capture:
  flush_method: inbox             # inbox = direct write (default); uri/rest require obsidian_mode: true
  inbox_folder: ""                # empty = vault root
  daily_notes_folder: Daily
  daily_notes_format: "%Y.%m.%d"
  log_section: Agenda
  log_prefix: "- "               # set to "logger:: " for Obsidian Dataview
  daily_notes_template: "Resources/Templates/Daily.md"

calendar:
  enabled: true
  calendars: []                   # empty = all calendars
  agenda_section: Agenda
  meetings_folder: Meetings
  meeting_template: ""            # vault-relative path to meeting note template
```

**`obsidian_mode`** is a per-vault flag. When `false` (default):
- Only `inbox` flush method is available
- Daily note creation uses plain template substitution (no Templater)
- `uri` or `rest` flush methods raise a clear error if configured

## Vault layout (active vault)

```
BrainTree/
  Daily/           YYYY.MM.DD.md — daily journal notes
  Meetings/        meeting notes linked to and from daily notes
  Notes/           YYYY.MM.DD-Author-ShortTitle.md — content notes
  Projects/        one note per project (create via project-init skill)
  Resources/
    Skills/        model-agnostic skill files (indexed and searchable)
    Templates/     {placeholder} templates (ignored from index)
  .nooscope/       nooscope.db — vector index (gitignored, rebuildable)
```

Links between notes use standard relative markdown: `[Title](../Folder/File.md)`. Each note typically links back to the daily note on which it was created.

## Testing

The test suite uses **pytest** with **pytest-cov** for coverage. 94 tests across 7 files.

```bash
.venv/bin/pytest                          # run all tests
.venv/bin/pytest tests/test_agenda.py     # run a single file
.venv/bin/pytest --cov=nooscope --cov-report=term-missing  # with coverage
```

**Test files:**
- `tests/test_capture.py` — log entry, template creation, flush methods, queue lifecycle, obsidian_mode guard
- `tests/test_agenda.py` — `_replace_agenda_section`, `_build_agenda_lines`, `inject_agenda`, CLI inject-agenda paths
- `tests/test_indexer.py` — parsing, chunking, MOC handling
- `tests/test_backends.py` — embedding backend availability checks
- `tests/test_mcp_tools.py` — MCP tool return shapes
- `tests/test_chunking.py` — heading-based chunk splitting
- `tests/test_writing.py` — `write_note`, `_write_vault_file`, path traversal guard, flush delegation

**Mocking pattern:** `calendar_reader` and `meeting_notes` use lazy imports inside functions to avoid requiring EventKit at startup. Always patch at the source module, not the call site:
```python
# Correct
patch("nooscope.calendar_reader.get_events_for_date", ...)
patch("nooscope.meeting_notes.create_meeting_note", ...)

# Wrong — name doesn't exist at module level
patch("nooscope.agenda_injector.get_events_for_date", ...)
```

**Obsidian-specific code** lives entirely in `nooscope/obsidian.py`. Patch `nooscope.obsidian.subprocess.run` (not `nooscope.capture.subprocess.run`) when testing URI flush behavior.

**Docstrings:** All public functions use Google style (`Args:`, `Returns:`, `Raises:`).

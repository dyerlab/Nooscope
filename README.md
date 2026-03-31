![](https://www.flickr.com/photo_download.gne?id=55179377424&secret=621ca9e2be&size=c&source=photoPageEngagement)

# Nooscope

A sidecar MCP (Model Context Protocol) server for Obsidian vaults. Maintains a local SQLite vector index of your markdown notes and exposes semantic search, navigation, and capture tools to Claude Code and Claude Desktop.

A longer discussion and rationalle for this project was posted to my [blog](https://www.rodneydyer.com/your-vault-your-vectors-building-a-local-first-mcp-server-for-obsidian/) on 29 March 2026.

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) running locally with `nomic-embed-text` pulled
- An Obsidian vault

```bash
ollama pull nomic-embed-text
```

## Installation

```bash
git clone <repo>
cd nooscope

# Create venv and install
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Install globally via pipx (required for Claude Desktop — see below)
pip install pipx
pipx install .
```

## Configuration

Copy the example config and edit for your vault:

```bash
cp nooscope.yaml.example nooscope.yaml
```

Minimum required config:

```yaml
vaults:
  - name: MyVault
    path: /path/to/your/obsidian/vault
    db_path: /path/to/your/obsidian/vault/nooscope.db

embeddings:
  semantic:
    backend: ollama
    model: nomic-embed-text
    dimensions: 768

chunking:
  max_tokens: 512
  strategy: headings

mcp:
  transport: stdio

capture:
  flush_method: inbox            # uri | inbox | rest
  obsidian_vault_name: MyVault   # must match Obsidian's vault display name exactly
  inbox_folder: _inbox
  daily_notes_folder: Daily
  daily_notes_format: "%Y-%m-%d"
  log_section: Notes
  daily_notes_template: ""       # optional: path to Templater template within vault
```

Set the config path via environment variable (add to your shell profile):

```bash
export NOOSCOPE_CONFIG=/path/to/nooscope.yaml
```

## Build the index

```bash
nooscope rebuild    # initial full index (~30-60 min for large vaults)
nooscope watch      # start incremental watcher (run in background or as a service)
```

## Registering with Claude Code CLI

```bash
claude mcp add --scope user \
  -e NOOSCOPE_CONFIG=/path/to/nooscope.yaml \
  -- nooscope /Users/<you>/.local/bin/nooscope serve
```

Verify:

```bash
claude mcp list
```

## Registering with Claude Desktop

Claude Desktop runs in a macOS sandbox and cannot access files on external volumes. Use the pipx-installed binary (which lives in `~/.local/bin/`, always accessible):

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nooscope": {
      "command": "/Users/<you>/.local/bin/nooscope",
      "args": ["serve"],
      "env": {
        "NOOSCOPE_CONFIG": "/path/to/nooscope.yaml"
      }
    }
  }
}
```

Restart Claude Desktop. Check Settings → Developer for server status.

**After updating nooscope code**, sync the pipx copy:

```bash
pipx install --force /path/to/nooscope
```

## CLI reference

```bash
nooscope rebuild                              # Full vault reindex
nooscope watch                                # Incremental file watcher
nooscope serve                                # Start MCP server (stdio)

nooscope log "text" --refs "Note,Person"      # Append logger:: to today's daily note
nooscope log "text" --date 2026-04-01         # Log to a specific date
nooscope capture "text" --title "..." --tags "t1,t2"  # Queue a structured note
nooscope queue                                # List pending captures
nooscope flush                                # Flush queued captures to Obsidian inbox
nooscope flush-logs                           # Retry pending log entries
```

## MCP tools

| Tool | Description |
|---|---|
| `search` | Semantic vector search. Returns `file_path`, `folder`, `title`, `similarity`, `content`, `source` |
| `cross_space_search` | Compare similarity scores across two embedding types |
| `read_note` | Full note content + frontmatter + backlinks |
| `list_notes` | Browse by folder, tags, or recency |
| `get_backlinks` | All notes linking to a given note |
| `vault_stats` | Index counts and status |
| `capture_thought` | Queue a structured note for flush to `_inbox/` |
| `log_thought` | Append `logger::` entry to today's daily note |
| `rebuild` | Trigger full vault reindex |

## Capture modes

**Structured capture** (`capture_thought` / `nooscope capture`): queued in SQLite, flushed as new `.md` files to `_inbox/` via `nooscope flush`.

**Log entry** (`log_thought` / `nooscope log`): appends a `logger::` bullet to the `## Notes` section of today's daily note. If the note doesn't exist, writes it from the Templater template (preserving `<% %>` tags for Obsidian to process on first open). Entry is always queued first — never lost — and retried automatically when the watcher sees the note created.

## Development

```bash
source .venv/bin/activate
pytest                              # run all tests
pytest tests/test_capture.py        # run a single file
```

`nooscope.yaml` and `nooscope.db` are gitignored — never commit vault-specific config or the index.

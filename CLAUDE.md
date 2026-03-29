# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nooscope is a Python-based MCP (Model Context Protocol) server that maintains a local, multi-embedding vector index of Obsidian markdown vaults. It exposes semantic search and corpus analysis tools to AI assistants via MCP.

**Status:** Design-phase project. `DESIGN.md` is the authoritative specification. Implementation has not yet begun.

The primary vault is at `/Volumes/Developer/BrainTree`. The project note is at `Projects/Nooscope.md` in that vault. Architectural background and motivation are documented in these vault notes:

- `PKM-Requirements-for-Vector-MCP-Integration`
- `Canonical-Truth-in-Markdown-Vectors-as-Derivative`
- `Smart-Vector-Chunking-via-Obsidian-Transclusions`
- `MOC-as-Semantic-Barycenter`
- `Semantic-Constellation-and-Convex-Hull-of-Research-Output`
- `ReviewerNumberTwo-as-Research-Content-Substrate`

## Commands

Once `pyproject.toml` exists:

```bash
pip install -e .          # Install in editable mode
pytest                    # Run all tests
pytest tests/test_indexer.py  # Run a single test file
nooscope rebuild          # Full vault reindex
nooscope watch            # Start incremental file watcher
nooscope serve            # Start MCP server
```

## Architecture

```
Vault (markdown) → nooscope-watcher (watchdog/fsevents) → nooscope.db (SQLite + sqlite-vec)
                                                                   ↓
                                                    MCP Server (stdio or HTTP)
                                                    → Claude Code / Claude Desktop
```

**Key architectural principle:** The vault is canonical truth. `nooscope.db` is a rebuildable derivative that travels with the vault via Obsidian Sync/iCloud.

### Module responsibilities

- `nooscope/cli.py` — Entry points: `rebuild`, `watch`, `serve`
- `nooscope/config.py` — Load and validate `nooscope.yaml`
- `nooscope/db.py` — SQLite schema init, migrations, CRUD
- `nooscope/indexer.py` — Parse vault files, chunk by headings, coordinate embedding
- `nooscope/watcher.py` — Incremental updates via watchdog fsevents
- `nooscope/barycenter.py` — MOC barycenter computation and cache management
- `nooscope/mcp_server.py` — MCP tool definitions and server entry point
- `nooscope/backends/` — Embedding backends (all implement `EmbeddingBackend` from `base.py`)
- `nooscope/tools/` — MCP tool groups: `search`, `navigation`, `analysis`, `management`

### Embedding backends

All backends implement `embed(texts: list[str]) -> list[list[float]]` and `is_available() -> bool`.

| Backend | Platform | Notes |
|---|---|---|
| `OllamaBackend` | Any | **Default.** Requires Ollama running with `nomic-embed-text` |
| `MLXBackend` | macOS/Apple Silicon | Requires `mlx-lm` package |
| `AppleNLBackend` | macOS | Uses `NaturalLanguage.framework` via PyObjC |
| `OpenAIBackend` | Any | Requires `OPENAI_API_KEY` |
| `FDLBackend` | Any | Frequency-dependent loading, domain vocabulary fingerprint |

### Document chunking

- Under context window → single row, `chunk_index=0`
- Over limit with headings → split at `##`, each chunk is a separate row; parent gets a barycenter embedding
- MOC notes (transclusion-only) → `is_moc=true`, embedding is barycenter of referenced files

### Database tables

`vaults`, `documents`, `embeddings`, `barycenters`, `watcher_state` — full schema in `DESIGN.md`.

## Configuration

`nooscope.yaml` (gitignored; use `nooscope.yaml.example` as template). Defines vault paths, embedding backends per type, chunking strategy, and MCP transport (`stdio` or `http`).

## Implementation Phases

1. Core indexer (`db.py`, Ollama backend, `indexer.py`, `rebuild` CLI)
2. Watcher (`watcher.py`, LaunchAgent)
3. MCP server — search + navigation tools
4. Barycenter + MOC support
5. Corpus analysis tools (`find_outliers`, `temporal_drift`, `novelty_score`)
6. Additional backends (MLX, AppleNL, FDL) + multi-embedding indexing

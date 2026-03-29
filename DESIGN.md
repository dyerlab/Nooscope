# Nooscope — Design Specification

**A multi-embedding sidecar and MCP server for Obsidian vaults.**

---

## Problem Statement

Obsidian is an excellent canonical knowledge store — plain markdown, local-first, well-synced, human-readable. But it has no vector index. This means:

- An AI assistant (Claude, etc.) starting a new session has no fast path to relevant vault context
- Semantic search requires reading many files manually
- Cross-document analysis (similarity, drift, gaps, novelty) is impossible without external tooling
- Conversations start cold even when the vault has deep, relevant prior work

The goal of Nooscope is to maintain a local, authoritative, multi-embedding index of one or more markdown vaults, expose it via an MCP server, and stay entirely on-device.

---

## Core Principles

1. **Vault is canonical truth.** The SQLite sidecar is a derivative, rebuildable artifact. Nooscope never writes to vault files (except optionally to inject frontmatter it is explicitly asked to update).
2. **Vectors are derivatives.** If the sidecar is deleted, `nooscope rebuild` reconstructs it from the vault in full.
3. **Multiple embeddings are first-class.** Each document chunk can carry N embedding vectors, one per backend. They are independent projections into different high-dimensional spaces with the same mathematical properties.
4. **Cross-platform by default.** The default embedding backend (Ollama) runs on any OS. Apple Silicon backends (MLX, NLEmbedding) are optional accelerators, not requirements.
5. **MCP interface is the primary API.** All AI interaction goes through MCP tools. No bespoke HTTP API.

---

## Architecture

```
Obsidian Vault(s)  ←──────────────────────────────────────────────┐
   (markdown files)                                                │
        │                                                          │ Obsidian Sync /
        │ fsevents (watchdog)                                      │ iCloud syncs vault
        ▼                                                          │ + sidecar together
   nooscope-watcher                                                │
        │                                                          │
        ▼                                                     ┌────┴────┐
   nooscope.db  ◄──── nooscope rebuild (full reindex)        │  Other  │
   (sqlite-vec)                                               │ Devices │
        │                                                     └─────────┘
        ▼
   MCP Server (stdio or HTTP)
        │
        ├── Claude Code (local session)
        ├── Claude Desktop (custom connector)
        └── Claude.ai (remote, if HTTP mode)
```

The sidecar database (`nooscope.db`) lives in the vault root (or a configurable path). It travels with the vault via Obsidian Sync or iCloud, so any device with Nooscope installed picks up the index automatically.

---

## Database Schema

```sql
-- One row per vault registered with this Nooscope instance
CREATE TABLE vaults (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,   -- e.g. 'braintree', 'research'
    root_path   TEXT NOT NULL,
    description TEXT,
    created_at  REAL DEFAULT (unixepoch())
);

-- One row per document chunk. chunk_index=0 is the full document.
-- chunk_index 1..N are heading-based splits for oversized documents.
CREATE TABLE documents (
    id           INTEGER PRIMARY KEY,
    vault_id     INTEGER NOT NULL REFERENCES vaults(id),
    file_path    TEXT NOT NULL,          -- relative to vault root
    title        TEXT,
    content      TEXT,                   -- plaintext, stripped of frontmatter
    frontmatter  TEXT,                   -- JSON blob of YAML frontmatter
    content_hash TEXT NOT NULL,          -- SHA-256, for change detection
    modified_at  REAL,                   -- file mtime (Unix)
    word_count   INTEGER,
    chunk_index  INTEGER DEFAULT 0,
    section      TEXT,                   -- heading of this chunk, null if full doc
    parent_id    INTEGER REFERENCES documents(id),  -- null if chunk_index=0
    is_moc       BOOLEAN DEFAULT FALSE,  -- true if composed primarily of transclusions
    indexed_at   REAL DEFAULT (unixepoch()),
    UNIQUE(vault_id, file_path, chunk_index)
);

-- One row per (document, embedding_type). Stores the raw vector blob.
CREATE TABLE embeddings (
    id             INTEGER PRIMARY KEY,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding_type TEXT NOT NULL,   -- 'semantic', 'fdl', 'nl', 'mlx'
    model          TEXT NOT NULL,   -- specific model ID e.g. 'nomic-embed-text'
    vector         BLOB NOT NULL,   -- packed Float32 little-endian
    dimensions     INTEGER NOT NULL,
    created_at     REAL DEFAULT (unixepoch()),
    UNIQUE(document_id, embedding_type)
);

-- Barycenter cache for MOC notes. Recomputed when any component changes.
-- Also used for section-level barycenters of chunked documents.
CREATE TABLE barycenters (
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding_type TEXT NOT NULL,
    vector         BLOB NOT NULL,
    component_ids  TEXT NOT NULL,   -- JSON array of document_ids used
    component_count INTEGER,
    updated_at     REAL DEFAULT (unixepoch()),
    PRIMARY KEY(document_id, embedding_type)
);

-- Tracks which files the watcher has seen, for incremental updates
CREATE TABLE watcher_state (
    vault_id    INTEGER NOT NULL REFERENCES vaults(id),
    file_path   TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    modified_at  REAL,
    PRIMARY KEY(vault_id, file_path)
);
```

---

## Embedding Backends

All backends implement a common interface:

```python
class EmbeddingBackend:
    name: str          # e.g. 'ollama', 'mlx', 'openai'
    model: str         # e.g. 'nomic-embed-text', 'mlx-community/...'
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def is_available(self) -> bool: ...
```

| Backend | Type | Platform | Notes |
|---|---|---|---|
| `OllamaBackend` | Local | Any | **Default.** Requires `ollama` running. `nomic-embed-text` recommended. |
| `MLXBackend` | Local | macOS / Apple Silicon | Fast, low power. Requires `mlx-lm` Python package. |
| `AppleNLBackend` | Local | macOS | Uses `NaturalLanguage.framework` via PyObjC. Zero model download. |
| `OpenAIBackend` | Cloud | Any | Fallback. Requires `OPENAI_API_KEY`. `text-embedding-3-small`. |
| `FDLBackend` | Local | Any | Frequency-dependent loading. Domain vocabulary fingerprint. Custom implementation. |

Default config uses `OllamaBackend` for `semantic` and nothing else. Additional embedding types are opt-in per vault.

---

## Chunking Strategy

1. **Under context window** → embed as single document (`chunk_index=0` only)
2. **Over context window, heading-structured** → split at `##` headings, each chunk gets its own row. Parent row (`chunk_index=0`) gets a barycenter embedding computed from chunk embeddings.
3. **Over context window, unstructured** → LLM pass to identify semantic boundaries, then split. Flagged as `requires_refactor=true` in frontmatter candidate list.
4. **MOC (transclusions only)** → `is_moc=true`. Embedding is barycenter of all `![[referenced]]` file embeddings. Recomputed when any component changes.
5. **Hybrid MOC** → prose sections embedded directly + included as weighted component in barycenter.

Barycenter weighting options (per vault config):
- `uniform` — equal weight (default)
- `by_length` — proportional to word count
- `by_recency` — recently modified sections weighted higher

---

## MCP Tool Interface

### Core search tools

```
search(query, embedding_type="semantic", vault=null, limit=10, threshold=0.6)
    → [{file_path, title, section, score, snippet}]

cross_space_search(query, high_in="semantic", low_in="fdl", vault=null, limit=10)
    → [{file_path, title, score_high, score_low, delta, snippet}]
    Use case: "same topic, different vocabulary" or "same vocabulary, different framing"
```

### Navigation tools

```
read_note(file_path, vault=null)
    → {frontmatter, content, backlinks}

list_notes(folder=null, vault=null, tags=null, limit=50)
    → [{file_path, title, modified_at, word_count}]

get_backlinks(file_path, vault=null)
    → [{file_path, title, context_snippet}]
```

### Corpus analysis tools

```
get_barycenter(file_path, embedding_type="semantic", vault=null)
    → {vector_summary, component_files, component_count}

find_outliers(embedding_type="semantic", vault=null, n=10)
    → [{file_path, title, distance_from_centroid}]
    Use case: find notes that are semantic outliers in the vault

temporal_drift(embedding_type="semantic", date_field="date", vault=null)
    → [{year, centroid_position, nearest_notes}]
    Use case: how has the vault's center of mass shifted over time?

novelty_score(file_path, embedding_type="semantic", vault=null)
    → {score, interpretation, nearest_neighbors}
    Use case: how novel is this note relative to the existing corpus?
```

### Vault management tools

```
vault_stats(vault=null)
    → {note_count, indexed_count, pending_count, embedding_types, last_indexed}

rebuild(vault=null, embedding_type=null)
    → {reindexed, skipped, errors}
```

---

## Configuration

`nooscope.yaml` in the project root (not in the vault):

```yaml
vaults:
  - name: braintree
    path: /Volumes/Developer/BrainTree
    db_path: /Volumes/Developer/BrainTree/nooscope.db   # travels with vault via sync

embeddings:
  semantic:
    backend: ollama
    model: nomic-embed-text
    dimensions: 768
  # Uncomment to add additional projections:
  # fdl:
  #   backend: fdl
  #   model: custom
  #   dimensions: 512
  # nl:
  #   backend: apple_nl
  #   model: native
  #   dimensions: 512

chunking:
  max_tokens: 512
  strategy: headings          # headings | semantic | fixed
  moc_barycenter_weight: uniform

mcp:
  transport: stdio            # stdio | http
  # host: 127.0.0.1           # http only
  # port: 8765                # http only
```

---

## Project Structure

```
Nooscope/
├── DESIGN.md                 ← this file
├── README.md
├── nooscope.yaml             ← configuration (gitignored for path privacy)
├── nooscope.yaml.example     ← committed template
├── pyproject.toml
├── requirements.txt
│
├── nooscope/
│   ├── __init__.py
│   ├── cli.py                ← entry points: rebuild, watch, serve
│   ├── config.py             ← load/validate nooscope.yaml
│   ├── db.py                 ← SQLite init, migrations, CRUD
│   ├── indexer.py            ← parse vault files, chunk, coordinate embedding
│   ├── watcher.py            ← fsevents via watchdog, incremental updates
│   ├── barycenter.py         ← MOC barycenter computation, cache management
│   ├── mcp_server.py         ← MCP tool definitions and server entry point
│   │
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── base.py           ← EmbeddingBackend abstract class
│   │   ├── ollama.py
│   │   ├── mlx.py
│   │   ├── apple_nl.py
│   │   ├── openai.py
│   │   └── fdl.py
│   │
│   └── tools/                ← one file per MCP tool group
│       ├── search.py
│       ├── navigation.py
│       ├── analysis.py
│       └── management.py
│
├── tests/
│   ├── fixtures/             ← small sample vault for tests
│   ├── test_indexer.py
│   ├── test_chunking.py
│   ├── test_backends.py
│   └── test_mcp_tools.py
│
└── scripts/
    ├── install-launchagent.sh
    └── nooscope-watcher.plist.template
```

---

## Implementation Phases

### Phase 1 — Core indexer (no MCP yet)
- `db.py` schema init
- `backends/ollama.py` (semantic only)
- `indexer.py` — parse frontmatter, chunk by headings, embed, store
- `cli.py rebuild` — full vault reindex from scratch
- Goal: run `nooscope rebuild` and have a populated `nooscope.db`

### Phase 2 — Watcher
- `watcher.py` — fsevents via watchdog, incremental updates on file change/create/delete
- `cli.py watch` — foreground watcher with logging
- LaunchAgent plist for background operation

### Phase 3 — MCP server (core tools)
- `mcp_server.py` + `tools/search.py` + `tools/navigation.py`
- `search`, `read_note`, `list_notes`, `vault_stats`
- Connect to Claude Desktop or Claude Code via stdio transport
- Goal: Claude can query the vault mid-conversation

### Phase 4 — Barycenter + MOC support
- `barycenter.py`
- MOC detection, barycenter computation and caching
- Chunked document barycenters

### Phase 5 — Corpus analysis tools
- `tools/analysis.py`
- `find_outliers`, `temporal_drift`, `novelty_score`, `cross_space_search`

### Phase 6 — Additional embedding backends
- `backends/mlx.py` (Apple Silicon)
- `backends/apple_nl.py` (macOS NaturalLanguage.framework)
- `backends/fdl.py` (frequency-dependent loading, from ReviewerNumberTwo)
- Multi-embedding indexing in `indexer.py`

---

## Open Questions

1. **sqlite-vec vs. manual cosine similarity** — sqlite-vec extension provides ANN search natively; manual numpy cosine is simpler to install. For vault sizes under ~50k notes, manual is fast enough. sqlite-vec is preferred if available.
2. **Sidecar sync** — `nooscope.db` travels with the vault. On a device that has Nooscope installed, it uses the synced db directly. On a device without Nooscope, the db is ignored. This means the index is always current on the primary machine and available (read-only, stale until next rebuild) on secondary devices.
3. **ReviewerNumberTwo integration** — RN2's existing SQLite schema can either be migrated into a Nooscope `vault` with `type=research` or remain independent with a thin MCP adapter. Decision deferred to Phase 6.
4. **Wikilink graph** — backlinks are useful for navigation. Parsing `[[wikilinks]]` during indexing and storing as a graph table is a Phase 3 or 4 addition.

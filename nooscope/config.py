from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class VaultConfig:
    name: str
    path: str
    db_path: str


@dataclass
class EmbeddingConfig:
    backend: str
    model: str
    dimensions: int


@dataclass
class ChunkingConfig:
    max_tokens: int = 512
    strategy: str = "headings"
    moc_barycenter_weight: str = "uniform"


@dataclass
class MCPConfig:
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class CaptureConfig:
    flush_method: str = "uri"          # uri | inbox | rest
    inbox_folder: str = "_inbox"
    obsidian_vault_name: str = ""      # must match Obsidian's vault display name
    rest_port: int = 27123
    rest_api_key: str = ""
    daily_notes_folder: str = "Resources/Daily"
    daily_notes_format: str = "%Y-%m-%d"
    log_section: str = "Notes"         # heading name (without ##) to append logger:: entries
    daily_notes_template: str = ""     # vault-relative path to Templater template, e.g. Resources/Templates/Daily Note.md


@dataclass
class Config:
    vaults: list[VaultConfig]
    embedding_types: dict[str, EmbeddingConfig]
    chunking: ChunkingConfig
    mcp: MCPConfig
    capture: CaptureConfig = field(default_factory=CaptureConfig)


def load_config(path: str | None = None) -> Config:
    if path is None:
        path = os.environ.get("NOOSCOPE_CONFIG")
    if path is None:
        candidates = [
            Path("nooscope.yaml"),
            Path.home() / ".config" / "nooscope" / "nooscope.yaml",
        ]
        for c in candidates:
            if c.exists():
                path = str(c)
                break
    if path is None:
        raise FileNotFoundError("nooscope.yaml not found")

    with open(path) as f:
        raw = yaml.safe_load(f)

    vaults = [
        VaultConfig(
            name=v["name"],
            path=v["path"],
            db_path=v["db_path"],
        )
        for v in raw.get("vaults", [])
    ]

    embedding_types: dict[str, EmbeddingConfig] = {}
    for etype, ecfg in raw.get("embeddings", {}).items():
        embedding_types[etype] = EmbeddingConfig(
            backend=ecfg["backend"],
            model=ecfg["model"],
            dimensions=ecfg["dimensions"],
        )

    raw_chunking = raw.get("chunking", {})
    chunking = ChunkingConfig(
        max_tokens=raw_chunking.get("max_tokens", 512),
        strategy=raw_chunking.get("strategy", "headings"),
        moc_barycenter_weight=raw_chunking.get("moc_barycenter_weight", "uniform"),
    )

    raw_mcp = raw.get("mcp", {})
    mcp = MCPConfig(
        transport=raw_mcp.get("transport", "stdio"),
        host=raw_mcp.get("host", "127.0.0.1"),
        port=raw_mcp.get("port", 8765),
    )

    raw_cap = raw.get("capture", {})
    capture = CaptureConfig(
        flush_method=raw_cap.get("flush_method", "uri"),
        inbox_folder=raw_cap.get("inbox_folder", "_inbox"),
        obsidian_vault_name=raw_cap.get("obsidian_vault_name", ""),
        rest_port=raw_cap.get("rest_port", 27123),
        rest_api_key=raw_cap.get("rest_api_key", ""),
        daily_notes_folder=raw_cap.get("daily_notes_folder", "Resources/Daily"),
        daily_notes_format=raw_cap.get("daily_notes_format", "%Y-%m-%d"),
        log_section=raw_cap.get("log_section", "Notes"),
        daily_notes_template=raw_cap.get("daily_notes_template", ""),
    )

    return Config(
        vaults=vaults,
        embedding_types=embedding_types,
        chunking=chunking,
        mcp=mcp,
        capture=capture,
    )

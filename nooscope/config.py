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
class Config:
    vaults: list[VaultConfig]
    embedding_types: dict[str, EmbeddingConfig]
    chunking: ChunkingConfig
    mcp: MCPConfig


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

    return Config(vaults=vaults, embedding_types=embedding_types, chunking=chunking, mcp=mcp)

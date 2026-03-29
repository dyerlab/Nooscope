from __future__ import annotations

from nooscope.backends.base import EmbeddingBackend


class FDLBackend(EmbeddingBackend):
    name = "fdl"

    def __init__(self, model: str = "custom", dimensions: int = 512) -> None:
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("FDLBackend is not yet implemented")

    def is_available(self) -> bool:
        return False

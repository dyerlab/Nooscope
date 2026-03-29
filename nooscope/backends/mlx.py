from __future__ import annotations

from nooscope.backends.base import EmbeddingBackend


class MLXBackend(EmbeddingBackend):
    name = "mlx"

    def __init__(self, model: str, dimensions: int) -> None:
        try:
            import mlx_lm  # noqa: F401
        except ImportError:
            raise ImportError("mlx-lm is required for MLXBackend: pip install nooscope[mlx]")
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("MLXBackend.embed not yet implemented")

    def is_available(self) -> bool:
        try:
            import mlx_lm  # noqa: F401
            return True
        except ImportError:
            return False

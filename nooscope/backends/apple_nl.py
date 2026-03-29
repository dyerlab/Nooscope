from __future__ import annotations

from nooscope.backends.base import EmbeddingBackend


class AppleNLBackend(EmbeddingBackend):
    name = "apple_nl"

    def __init__(self, model: str = "native", dimensions: int = 512) -> None:
        try:
            import NaturalLanguage  # noqa: F401
        except ImportError:
            raise ImportError(
                "PyObjC NaturalLanguage is required: pip install nooscope[apple]"
            )
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("AppleNLBackend.embed not yet implemented")

    def is_available(self) -> bool:
        try:
            import NaturalLanguage  # noqa: F401
            return True
        except ImportError:
            return False

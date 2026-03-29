from abc import ABC, abstractmethod


class EmbeddingBackend(ABC):
    name: str
    model: str
    dimensions: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def is_available(self) -> bool: ...

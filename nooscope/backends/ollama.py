from __future__ import annotations

import httpx

from nooscope.backends.base import EmbeddingBackend

OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaBackend(EmbeddingBackend):
    name = "ollama"

    def __init__(self, model: str = "nomic-embed-text", dimensions: int = 768) -> None:
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()
            return data["embeddings"]

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{OLLAMA_BASE_URL}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

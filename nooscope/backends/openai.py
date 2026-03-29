from __future__ import annotations

import os

from nooscope.backends.base import EmbeddingBackend


class OpenAIBackend(EmbeddingBackend):
    name = "openai"

    def __init__(self, model: str = "text-embedding-3-small", dimensions: int = 1536) -> None:
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            import openai
        except ImportError:
            raise ImportError("openai package required: pip install nooscope[openai]")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable not set")

        client = openai.OpenAI(api_key=api_key)
        response = client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]

    def is_available(self) -> bool:
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("OPENAI_API_KEY"))

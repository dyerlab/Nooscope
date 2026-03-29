from __future__ import annotations

import pytest

from nooscope.backends.ollama import OllamaBackend


@pytest.fixture
def ollama():
    return OllamaBackend(model="nomic-embed-text", dimensions=768)


def test_ollama_is_available_returns_bool(ollama):
    result = ollama.is_available()
    assert isinstance(result, bool)


@pytest.mark.skipif(
    not OllamaBackend().is_available(),
    reason="Ollama not running",
)
def test_ollama_embed_single(ollama):
    result = ollama.embed(["hello world"])
    assert len(result) == 1
    assert len(result[0]) == 768
    assert all(isinstance(v, float) for v in result[0])


@pytest.mark.skipif(
    not OllamaBackend().is_available(),
    reason="Ollama not running",
)
def test_ollama_embed_batch(ollama):
    texts = ["first text", "second text", "third text"]
    result = ollama.embed(texts)
    assert len(result) == 3
    for vec in result:
        assert len(vec) == 768


@pytest.mark.skipif(
    not OllamaBackend().is_available(),
    reason="Ollama not running",
)
def test_ollama_embed_returns_floats(ollama):
    result = ollama.embed(["test"])
    assert all(isinstance(v, float) for v in result[0])


def test_fdl_backend_not_available():
    from nooscope.backends.fdl import FDLBackend
    b = FDLBackend()
    assert not b.is_available()


def test_fdl_backend_raises_not_implemented():
    from nooscope.backends.fdl import FDLBackend
    b = FDLBackend()
    with pytest.raises(NotImplementedError):
        b.embed(["test"])

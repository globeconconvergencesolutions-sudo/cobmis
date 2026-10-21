import pytest

from app.llm.ollama_client import OllamaClient, OllamaError
from tests.conftest import fake_ollama_transport


def test_chat_and_embed(settings):
    client = OllamaClient(settings, transport=fake_ollama_transport([], embed_dim=settings.embed_dim))
    assert client.chat([{"role": "user", "content": "hi"}]) == "ready"
    vecs = client.embed(["a", "b"])
    assert len(vecs) == 2 and len(vecs[0]) == settings.embed_dim


def test_embed_dimension_mismatch_is_an_error(settings):
    client = OllamaClient(settings, transport=fake_ollama_transport([], embed_dim=settings.embed_dim + 1))
    with pytest.raises(OllamaError, match="dim"):
        client.embed(["a"])

import json

import httpx
import pytest

from app.core.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, profile="small")


def fake_ollama_transport(models: list[str], embed_dim: int = 768) -> httpx.MockTransport:
    """In-process stand-in for the Ollama HTTP API, for unit tests only."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if path == "/api/chat":
            body = json.loads(request.content)
            assert body["think"] is False
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "ready"}, "done": True})
        if path == "/api/embed":
            body = json.loads(request.content)
            return httpx.Response(200, json={"embeddings": [[0.0] * embed_dim for _ in body["input"]]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)

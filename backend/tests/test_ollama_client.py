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


def test_every_call_sets_num_ctx_and_chat_disables_thinking(settings):
    import json

    import httpx

    seen = []
    inner = fake_ollama_transport([], embed_dim=settings.embed_dim)

    def handler(request):
        if request.method == "POST":
            seen.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/api/chat" and json.loads(request.content).get("stream"):
            return httpx.Response(200, text='{"message":{"content":"ok"},"done":true}\n')
        return inner.handle_request(request)

    client = OllamaClient(settings, transport=httpx.MockTransport(handler))
    client.chat([{"role": "user", "content": "hi"}])
    assert list(client.chat_stream([{"role": "user", "content": "hi"}])) == ["ok"]
    client.embed(["x"])

    assert [p for p, _ in seen] == ["/api/chat", "/api/chat", "/api/embed"]
    for path, body in seen:
        assert body["options"]["num_ctx"] == settings.num_ctx == 8192
        if path == "/api/chat":
            assert body["think"] is False


def test_mock_backend_needs_no_server():
    from app.core.config import Settings
    from app.llm.mock import MOCK_REPLY

    s = Settings(_env_file=None, llm_backend="mock", ollama_url="http://127.0.0.1:1")
    client = OllamaClient(s)
    assert client.chat([{"role": "user", "content": "hi"}]) == MOCK_REPLY
    a, b, c = client.embed(["Turkana pending bills", "pending bills Turkana", "absorption rate"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    assert dot(a, b) > 0.99 and dot(a, c) < 0.5
    assert set(client.list_models()) >= {s.chat_model, s.embed_model}

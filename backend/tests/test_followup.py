from app.core.config import Settings
from app.llm.ollama_client import OllamaClient
from app.orchestrator.followup import rewrite_standalone
from tests.conftest import fake_ollama_transport


def test_rewrite_standalone_uses_chat(monkeypatch):
    # Capture the rewritten content from a fixed mock response.
    transport = fake_ollama_transport(["qwen3.5:9b", "bge-m3"], embed_dim=1024)

    # Override chat path via custom transport that returns a rewrite.
    import httpx
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}, {"name": "bge-m3"}]})
        if path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "What were Kilifi County pending bills in FY 2025/26?",
                    },
                    "done": True,
                },
            )
        if path == "/api/embed":
            body = json.loads(request.content)
            return httpx.Response(200, json={"embeddings": [[0.0] * 1024 for _ in body["input"]]})
        return httpx.Response(404)

    s = Settings(_env_file=None, profile="small", llm_backend="ollama", enforce_loopback=False)
    client = OllamaClient(s, transport=httpx.MockTransport(handler))
    out = rewrite_standalone(
        client,
        "and what about Kilifi?",
        prior_question="Top 5 counties by pending bills in FY 2025/26",
    )
    assert "Kilifi" in out
    assert "pending" in out.lower() or "FY" in out

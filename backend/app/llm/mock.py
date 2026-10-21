"""Deterministic in-process stand-in for the Ollama HTTP API.

For sandbox and CI testing only, where no Ollama server exists. Selected with
COB_LLM_BACKEND=mock. It never produces real model output: chat returns a
fixed marker string and embeddings are hash-derived unit vectors, so text
that shares words gets similar vectors and retrieval code can be exercised.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

import httpx

MOCK_REPLY = "[mock-llm] ready"


def mock_embedding(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for token in re.findall(r"\w+", text.lower()):
        h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def mock_ollama_transport(
    models: list[str],
    embed_dim: int,
    requests_seen: list[dict] | None = None,
) -> httpx.MockTransport:
    """`models` is what /api/tags reports. If `requests_seen` is given, each POST body is appended."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if requests_seen is not None and request.method == "POST":
            requests_seen.append({"path": path, **body})
        if path == "/api/version":
            return httpx.Response(200, json={"version": "mock"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if path == "/api/chat":
            msg = {"message": {"role": "assistant", "content": MOCK_REPLY}, "done": True}
            if body.get("stream"):
                return httpx.Response(200, content=(json.dumps(msg) + "\n").encode())
            return httpx.Response(200, json=msg)
        if path == "/api/embed":
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return httpx.Response(200, json={"embeddings": [mock_embedding(t, embed_dim) for t in inputs]})
        return httpx.Response(404, json={"error": f"mock: no route {path}"})

    return httpx.MockTransport(handler)

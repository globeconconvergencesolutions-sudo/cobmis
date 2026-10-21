"""Minimal Ollama client. Talks only to the loopback Ollama server; no hosted APIs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import Settings


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._http = httpx.Client(
            base_url=settings.ollama_url,
            timeout=settings.ollama_timeout_s,
            transport=transport,
            trust_env=False,  # never route loopback traffic through an HTTP(S)_PROXY
        )

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama unreachable at {self.settings.ollama_url}: {exc}") from exc
        if resp.status_code != 200:
            raise OllamaError(f"Ollama {path} returned {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def version(self) -> str:
        try:
            resp = self._http.get("/api/version", timeout=5)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc
        return resp.json().get("version", "unknown")

    def list_models(self) -> list[str]:
        try:
            resp = self._http.get("/api/tags", timeout=5)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc
        return [m["name"] for m in resp.json().get("models", [])]

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        data = self._post(
            "/api/chat",
            {
                "model": model or self.settings.chat_model,
                "messages": messages,
                "stream": False,
                # Qwen 3.x tags think by default; we want direct, short answers.
                "think": False,
                "options": {"temperature": temperature, "num_ctx": self.settings.num_ctx},
            },
        )
        return data["message"]["content"]

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> Iterator[str]:
        import json

        payload = {
            "model": model or self.settings.chat_model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": temperature, "num_ctx": self.settings.num_ctx},
        }
        try:
            with self._http.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    raise OllamaError(f"Ollama /api/chat returned {resp.status_code}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if piece := chunk.get("message", {}).get("content"):
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        data = self._post(
            "/api/embed",
            {"model": model or self.settings.embed_model, "input": texts},
        )
        vectors = data["embeddings"]
        for v in vectors:
            if len(v) != self.settings.embed_dim:
                raise OllamaError(
                    f"Embedding dim {len(v)} != configured embed_dim {self.settings.embed_dim}"
                )
        return vectors

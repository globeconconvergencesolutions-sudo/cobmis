"""Phase 0 smoke test: Ollama answers a prompt and embeds a string with the configured models.

Usage (from backend/):  python -m scripts.check_ollama
Exit code 0 only if every configured model is present and both calls succeed.
"""

from __future__ import annotations

import sys
import time

from app.core.config import get_settings
from app.llm.ollama_client import OllamaClient, OllamaError


def main() -> int:
    s = get_settings()
    client = OllamaClient(s)
    print(f"profile={s.profile} ollama={s.ollama_url}")
    try:
        print(f"ollama version: {client.version()}")
        available = client.list_models()
        failed = False
        for role, tag in (("chat", s.chat_model), ("sql", s.sql_model), ("embed", s.embed_model)):
            found = tag in available or f"{tag}:latest" in available
            print(f"  {role:5} {tag:28} {'OK' if found else 'MISSING  -> ollama pull ' + tag}")
            failed |= not found
        if failed:
            return 1

        t = time.perf_counter()
        reply = client.chat([{"role": "user", "content": "Reply with exactly the word: ready"}])
        print(f"chat ({time.perf_counter() - t:.1f}s): {reply.strip()[:80]!r}")

        t = time.perf_counter()
        vec = client.embed(["County Governments Budget Implementation Review, FY 2023/24"])[0]
        print(f"embed ({time.perf_counter() - t:.1f}s): dim={len(vec)} (configured {s.embed_dim})")
    except OllamaError as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        client.close()
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# COB County Budget Intelligence Agent

A fully local, private assistant over the Controller of Budget's consolidated county budget implementation review reports. Every service runs on `127.0.0.1`. All inference goes through a local Ollama server. No hosted AI APIs are used.

See `PLAN.md` for the full build plan and `DECISIONS.md` for design choices.

**Status: Phase 0 (setup).**

## Prerequisites

- Docker with Compose v2
- Python 3.11+
- [Ollama](https://ollama.com) installed natively (recommended for GPU access), or use the compose `ollama` profile

## Quick start

```bash
cp .env.example .env            # then change every change-me-* password
# pick COB_PROFILE=small|medium|large in .env (see config.yaml)

# 1. Postgres + pgvector (bound to 127.0.0.1:5433)
docker compose up -d

# 2. Ollama, loopback only
export OLLAMA_HOST=127.0.0.1
ollama serve &                  # or: docker compose --profile ollama up -d
ollama pull <chat_model>        # tags for your profile are in config.yaml
ollama pull <embed_model>

# 3. Backend
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
python -m scripts.check_ollama  # prompt + embedding smoke test
python -m app.main              # API on http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
```

`/health` returns 200 when Postgres has `vector` and `pg_trgm` and every configured model is pulled, and 503 with details otherwise.

## Tests

```bash
cd backend
pytest -q
# include the live-DB health test:
COB_TEST_DB_URL=postgresql+psycopg://cob_app:<COB_APP_PASSWORD>@127.0.0.1:5433/cob pytest -q
```

## Hardware profiles

| Profile | Rough target | Chat | SQL | Embeddings |
|---|---|---|---|---|
| small | 16 GB RAM, CPU or ≤8 GB VRAM | qwen3.5:9b | same | nomic-embed-text (768) |
| medium | 32 GB RAM, 16–24 GB VRAM | qwen3.6:27b | qwen3-coder:30b | bge-m3 (1024) |
| large | 64 GB+ RAM, 32 GB+ VRAM | qwen3.6:35b-a3b | qwen3-coder:30b | bge-m3 (1024) |

These tags are not yet verified (DECISIONS.md D-004). Any value can be overridden per variable, e.g. `COB_CHAT_MODEL=llama3.1:8b`. Changing the embedding model after ingestion means re-embedding, because `embed_dim` fixes the vector column size.

## Privacy notes

- Postgres, Ollama and the API all bind to `127.0.0.1`; the backend refuses to start if configured otherwise. Verify with `ss -tlnp` (Linux) or `lsof -iTCP -sTCP:LISTEN` (macOS).
- The SQL agent uses a read-only DB role with a 10 s statement timeout and no access to the `private` schema by default.
- Telemetry opt-out variables for dependencies are in `.env.example`.
- Full-disk encryption on the host is the minimum for data at rest (more detail in Phase 8).

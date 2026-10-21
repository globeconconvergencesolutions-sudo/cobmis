# COB County Budget Intelligence Agent

A fully local, private assistant over the Controller of Budget's consolidated county budget implementation review reports. Every service runs on `127.0.0.1`. All inference goes through a local Ollama server. No hosted AI APIs are used.

See `PLAN.md` for the full build plan and `DECISIONS.md` for design choices.

**Status: Phase 2 (parse, tables → SQL, chunk/embed CLI) done on the three FY 2025/26 reports; awaiting sign-off before Phase 3.**

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
python -m scripts.migrate       # extensions, schemas, tables, grants
python -m scripts.check_ollama  # prompt + embedding smoke test
python -m app.main              # API on http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
```

`/health` returns 200 when Postgres has `vector` and `pg_trgm` and every configured model is pulled, and 503 with details otherwise.

## Phase 1: download the COB reports

```bash
cd backend
python -m scripts.scrape_cob --limit 3     # trial: first 3 entries
python -m scripts.scrape_cob               # everything; ~2-3 s between requests
python -m scripts.scrape_cob --report      # print the manifest report (offline)
python -m scripts.scrape_cob --check-new   # later: only fetch newly listed reports
```

PDFs go to `data/raw/`, metadata to `data/manifest.json`, and the fetched listing page to `data/listing_snapshot.html`. Reruns skip files already on disk with a matching SHA-256 and retry `failed` entries. On Windows use the same commands from PowerShell, with the venv activated via `.venv\Scripts\Activate.ps1`.

## Phase 2: run the local pipeline (Windows, PowerShell)

Prerequisites: Docker Desktop running, Ollama running with `qwen3.5:9b` and `bge-m3`, Python 3.11+.

```powershell
git fetch origin claude/new-session-dkvc6f sample-pdfs
git checkout claude/new-session-dkvc6f
git pull

# 1. The three sample PDFs, from the sample-pdfs branch (never merge that branch).
#    git archive + tar avoids PowerShell pipes, which can corrupt binary data.
git archive -o samples.tar origin/sample-pdfs samples
tar -xf samples.tar -C data\raw --strip-components=1
Remove-Item samples.tar

# 2. Configuration (first time only): copy, then change every change-me-* password.
Copy-Item .env.example .env
#    In .env keep COB_PROFILE=small and COB_LLM_BACKEND=ollama.

# 3. Database (Postgres + pgvector on 127.0.0.1:5433)
docker compose up -d

# 4. Python environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
cd backend

# 5. Check Ollama: a test prompt and a test embedding with your models
python -m scripts.check_ollama

# 6. Ingest: migrate, parse (parallel, resumable), tables -> SQL, chunk, embed (resumable)
python -m scripts.ingest
#    Parsing takes about 2.5 min per report on 4 cores. Embedding ~5,500 chunks prints a rate
#    and ETA. Ctrl+C at any point is safe: rerun the same command to continue.
#    Options: --only 9M, --workers 6, --skip-embed, --embed-limit 200 (trial)

# 7. Ask (narrative/RAG path; numeric text-to-SQL is Phase 4)
python -m scripts.ask "What challenges did Kericho County face in the first nine months of FY 2025/26?"
python -m scripts.ask "pending bills" --county "Nairobi City" --period FY
python -m scripts.ask "own source revenue performance" --retrieve-only

# 8. Inspect quality
python -m scripts.table_qa            # writes data\qa\<report>.html: tables next to their PDF pages
python -m scripts.load_tables         # re-run table loading and print validation results
```

Every step can be re-run safely. To inspect the SQL data directly: `docker compose exec postgres psql -U cob_owner -d cob`, then for example `SELECT * FROM public_data.v_county_metrics WHERE county = 'Turkana' ORDER BY months, metric;`.

In this cloud sandbox there is no Ollama, so the same commands run with `COB_LLM_BACKEND=mock`. Mock vectors are hash-based stand-ins, and the mock chat model returns a fixed marker, never a real answer.

## Tests

```bash
cd backend
pytest -q
# include the live-DB health test:
COB_TEST_DB_URL=postgresql+psycopg://cob_app:<COB_APP_PASSWORD>@127.0.0.1:5433/cob pytest -q
```

`tests/test_db_pipeline.py` builds and drops its own `cob_test` database with the same migrations (it needs the owner password from `.env`) and is skipped when Postgres is not reachable.

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

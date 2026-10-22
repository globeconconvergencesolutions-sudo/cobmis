# COB County Budget Intelligence Agent (COBMIS)

A **fully local, private** assistant over the Controller of Budget (COB) consolidated county budget implementation review reports. Plain-English questions in; cited answers out. Every service binds to `127.0.0.1`. All language-model inference runs through a local [Ollama](https://ollama.com) server. **No hosted AI APIs** (no OpenAI, Anthropic, etc.).

| Doc | Purpose |
|---|---|
| **This README** | Restart on a new machine; commands; what is done vs remaining |
| [`docs/POC_OVERVIEW.md`](docs/POC_OVERVIEW.md) | Client / stakeholder brief: why Ollama, what the models do, capabilities now vs later |
| [`PLAN.md`](PLAN.md) | Full phased build plan |
| [`DECISIONS.md`](DECISIONS.md) | Design choices and rationale |

---

## Where we are (status)

We are building an **MVP / proof of concept**: prove that local models + COB PDFs can answer real county-budget questions with citations and refusals, before investing in a full UI and private-data product.

| Phase | Deliverable | Status |
|---|---|---|
| **0** Setup | Docker Postgres, Ollama, `/health`, hardware profiles | **Done** |
| **1** Scrape | Download COB PDFs → `data/raw/` + `manifest.json` | **Done** (trial: 3 × FY 2025/26 reports; full archive optional) |
| **2** Parse + tables → SQL | Parse PDFs, validate chapter-2 tables into Postgres, quarantine bad rows | **Done** on the three loaded reports |
| **3** RAG core | Chunk, embed, hybrid retrieval, cited narrative answers (CLI) | **Done** (CLI `scripts.ask`; county-strict retrieval + catalog route) |
| **3b** Catalog / meta | “What data do you have?”, “Do you have FY X?” from SQL only | **Done** |
| **4** SQL Q&A | Text-to-SQL for rankings, totals, comparisons | **Done** (CLI; sqlglot + `cob_readonly`; number-faithful narration) |
| **5** Full router | `rag` / `sql` / `both` / `catalog` + merge | **Done** (heuristic router; both-merge; follow-up `--prior`; `eval_run`) |
| **6** UI | Evidence Terminal: Login, Chat, Library, About, Admin | **Done** (Next.js + FastAPI SSE; session auth) |
| **7** Auth + private uploads | Restricted collections, private schema ACL tests | **Partial** (login/sessions/users in Phase 6; private upload ACL still to harden) |
| **8** Harden + demo | Offline test, backup, sync job | **Remaining** |

**Corpus on a typical PoC machine right now:** FY 2025/26 First Half, Nine Months, and Full Year (~2,650 pages, ~5,500 chunks, 47 counties in chapter-2 SQL). Older years exist on cob.go.ke; scrape without `--limit` when you want them.

---

## Prerequisites (new machine)

- **Docker Desktop** with Compose v2  
- **Python 3.11+**  
- **[Ollama](https://ollama.com)** installed natively (recommended for GPU), or Compose `ollama` profile  
- Disk: budget **several GB** for models + **~1–2 GB** for three sample PDFs (full archive closer to **1 GB** of PDFs)  
- For `COB_PROFILE=small` (verified): ~16 GB RAM, NVIDIA GPU with ~8 GB VRAM helpful but not mandatory for a slow CPU trial  

---

## Fresh machine: end-to-end bootstrap

### Windows (PowerShell)

```powershell
# 0. Clone and enter the repo
cd C:\path\to\cobmis

# 1. Config (first time only). Change every change-me-* password.
Copy-Item .env.example .env
# Keep COB_PROFILE=small and COB_LLM_BACKEND=ollama

# 2. Postgres + pgvector on 127.0.0.1:5433
docker compose up -d

# 3. Ollama (loopback) + models from config.yaml for your profile
$env:OLLAMA_HOST = "127.0.0.1"
# Start Ollama from the Start menu / tray, or: ollama serve
ollama pull qwen3.5:9b          # chat (+ sql on small profile)
ollama pull bge-m3              # embeddings (1024-dim)

# 4. Python env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt
cd backend

# 5. Roles + schema (safe to re-run)
python -m scripts.migrate
python -m scripts.check_ollama

# 6. Get PDFs — either scrape COB or restore samples
#    Option A: live scrape (polite; rate-limited)
python -m scripts.scrape_cob --limit 3
#    Option B: if you have a sample-pdfs branch/archive, place PDFs under data\raw\
#              and ensure data\manifest.json lists them (see scrape --report)

# 7. Ingest: parse → SQL tables → chunk → embed (resumable; Ctrl+C then re-run is OK)
python -m scripts.ingest

# 8. Ask
python -m scripts.ask "What data do you have?"
python -m scripts.ask "Do you have FY 2022/23?"
python -m scripts.ask "Top 5 counties by pending bills in FY 2025/26"
python -m scripts.ask "Turkana development absorption first nine months of FY 2025/26"
python -m scripts.ask "What challenges did Kericho County face in the first nine months of FY 2025/26?"
python -m scripts.ask "challenges" --county Kericho --period 9M --retrieve-only
```

### Linux / macOS

```bash
cp .env.example .env   # edit passwords
docker compose up -d
export OLLAMA_HOST=127.0.0.1
ollama serve &         # or system service
ollama pull qwen3.5:9b && ollama pull bge-m3

python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
python -m scripts.migrate
python -m scripts.check_ollama
python -m scripts.scrape_cob --limit 3
python -m scripts.ingest
python -m scripts.ask "What data do you have?"
```

Optional API smoke test:

```bash
python -m app.main          # http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
```

`/health` is **200** when Postgres has `vector` + `pg_trgm` and every configured model is pulled; otherwise **503** with details.

---

## What each major step is doing

| Step | Command | Why |
|---|---|---|
| **Scrape** | `scripts.scrape_cob` | Download official COB PDFs into `data/raw/`; write `data/manifest.json`. We do **not** invent figures — we answer from these files. |
| **Migrate** | `scripts.migrate` | Create DB roles, apply SQL migrations (documents, chunks, facts, catalog ACL stubs). |
| **Ingest** | `scripts.ingest` | Parse PDFs → JSON; load chapter-2 tables into `public_data`; chunk narrative/tables; embed chunks with **bge-m3**. |
| **Ask (catalog)** | `ask "What data…?"` | SQL-only meta answers (coverage, “do you have FY X?”). No model guessing. |
| **Ask (SQL)** | `ask "Top 5…pending bills…?"` | Model writes SELECT → sqlglot validates → `cob_readonly` runs it → answer from rows only. |
| **Ask (RAG)** | `ask "What challenges…?"` | Embed the question → hybrid search → **qwen** answers **only** from retrieved chunks, with citations. |
| **Ask (both)** | `ask "…challenges and OSR…?"` | SQL owns figures; RAG owns narrative; one merged answer + dual citations. |
| **Eval** | `scripts.eval_run` | Route accuracy + SQL/refuse checks against `eval/golden_questions.yaml`. |
| **QA tools** | `table_qa`, `load_tables` | Human-check extracted tables against PDF pages; re-print validation. |

Every ingest/scrape step is **resumable**. Re-run after Ctrl+C or a crash. Nothing from the working tree is mounted into the Postgres container; roles come from `migrate`, not a one-shot Docker init script.

---

## Demo questions that work today

```bash
cd backend
python -m scripts.ask "What data do you have?"
python -m scripts.ask "Do you have FY 2022/23?"
python -m scripts.ask "What can I ask?"
python -m scripts.ask "How up to date is this?"
python -m scripts.ask "Top 5 counties by pending bills in FY 2025/26"
python -m scripts.ask "Turkana development absorption first nine months of FY 2025/26"
python -m scripts.ask "Compare Nairobi City and Mombasa OSR performance H1 2025/26"
python -m scripts.ask "What challenges did Kericho County face in the first nine months of FY 2025/26?"
python -m scripts.ask "What challenges did Kericho face and what was their OSR performance in 9M FY 2025/26?"
python -m scripts.ask "and what about Kilifi?" --prior "Top 5 counties by pending bills in FY 2025/26"
python -m scripts.ask "challenges" --county Kericho --period 9M --retrieve-only
python -m scripts.eval_run --routes-only
```

- **Catalog** questions never call the chat model for numbers.  
- **SQL** questions print the answer plus a **How I got this** block (SQL + result rows + source pages).  
- **RAG** questions use retrieval + local chat; county filter is strict (national chapters only as labelled fallback).  
- **Both** merges SQL figures with RAG narrative; Sources (RAG) then How I got this (SQL).  
- **Follow-up** `--prior` / `--prior-answer` rewrites a short turn into a standalone question before routing.  
- Asking for a year that was never scraped (e.g. 2022/23 with only 2025/26 loaded) **refuses plainly**.

---

## Tests

```bash
cd backend
python -m pytest -q
# Live DB health (optional):
# COB_TEST_DB_URL=postgresql+psycopg://cob_app:<COB_APP_PASSWORD>@127.0.0.1:5433/cob python -m pytest -q
```

`tests/test_db_pipeline.py` / `test_catalog.py` / `test_retrieval.py` build a throwaway `cob_test` database when Postgres is up.

---

## Hardware profiles (`config.yaml`)

| Profile | Rough target | Chat | SQL | Embeddings |
|---|---|---|---|---|
| small | 16 GB RAM, CPU or ≤8 GB VRAM | qwen3.5:9b | qwen3.5:9b | bge-m3 (1024) |
| medium | 32 GB RAM, 16–24 GB VRAM | qwen3.6:27b | qwen3-coder:30b | bge-m3 (1024) |
| large | 64 GB+ RAM, 32 GB+ VRAM | qwen3.6:35b-a3b | qwen3-coder:30b | bge-m3 (1024) |

`small` is the verified PoC profile. Set `COB_PROFILE` in `.env`. Changing the **embedding** model after ingest requires a re-embed (vector size is fixed in the DB). Chat/SQL models can be swapped without re-embedding.

`COB_LLM_BACKEND=mock` is only for CI/sandboxes without Ollama — it does **not** produce real answers.

---

## Privacy (non-negotiable for the PoC story)

- Postgres, Ollama, and the API bind to **loopback only**; the backend refuses non-loopback config.  
- No paid/hosted LLM calls. After scrape + model pull, the demo can run with the network disabled.  
- Read-only DB role is reserved for the future SQL agent (`statement_timeout`, no `private` schema by default).  
- Full-disk encryption on the host is the minimum for data at rest.

Inspect SQL facts directly:

```bash
docker compose exec postgres psql -U cob_owner -d cob
# SELECT * FROM public_data.v_county_metrics WHERE county = 'Turkana' ORDER BY months, metric;
```

---

## Evidence Terminal UI (Phase 6)

```bash
# Terminal 1 — API (from backend/)
python -m scripts.migrate
python -m scripts.seed_admin          # admin@local / change-me-admin
python -m app.main                    # http://127.0.0.1:8000

# Terminal 2 — UI (from frontend/)
npm install
npm run dev                           # http://127.0.0.1:3000
```

Open **http://127.0.0.1:3000/login**, then demo:

1. Ask → Kericho challenges (RAG) · pending bills top-5 (SQL) · challenges + OSR (`both`)
2. Click citation chips / **Show evidence** → Sources + How I got this
3. **About** → same catalog as CLI  
4. **Library** → open a PDF  
5. **Admin** → health, jobs, users, audit CSV  

### What is running (four pieces)

| Piece | How it runs | Port | What it stores |
|---|---|---|---|
| **Postgres** | Docker `cob-agent-postgres-1` (`docker compose up -d`) | `127.0.0.1:5433` | Documents, chunks, SQL facts, **chat history**, users |
| **API** | `python -m app.main` from `backend/` (not a container) | `127.0.0.1:8000` | Nothing durable by itself — talks to Postgres + Ollama |
| **Next.js UI** | `npm run dev` from `frontend/` | `127.0.0.1:3000` | Nothing durable — proxies `/api` to the API |
| **Ollama** | Native app / `ollama serve` (or compose profile `ollama`) | `127.0.0.1:11434` | Downloaded models on disk |

`docker ps` only shows Postgres. The API and UI are normal OS processes.

### Start / stop without losing data

**Safe to stop anytime (data stays):**

```powershell
# Stop UI (Ctrl+C in the frontend terminal)
# Stop API (Ctrl+C in the backend terminal)
docker compose stop          # pauses Postgres; volume `pgdata` keeps everything
# Ollama: quit the tray app, or leave it running
```

**What survives a stop**

- Chat threads, answers, catalog docs, SQL facts → **Postgres volume** (until you `docker compose down -v`)
- PDFs under `data/raw/` → your disk
- Ollama models → Ollama’s local store

**What does not survive**

- An answer still generating when you kill the **API** mid-ask may never be written (worker dies with the process)
- The live “…” in the browser dies when you close the tab; if the API already **committed** the turn, reopen that chat from History

**Bring everything back**

```powershell
docker compose up -d
# start Ollama if needed
cd backend; python -m app.main
cd frontend; npm run dev
Invoke-RestMethod http://127.0.0.1:8000/health   # expect status ok
```

**Destructive (erases DB):** `docker compose down -v` — only if you intend to wipe. 

## What’s next (for the client pitch)

1. **Private collections** — harden ACL for restricted uploads (Phase 7).  
2. **Wider corpus** — scrape all years; optional OCR.  
3. **Harden + offline demo** — Phase 8 checklist.

Details and sellable language: **[`docs/POC_OVERVIEW.md`](docs/POC_OVERVIEW.md)**.

# Decisions log

Non-obvious choices and why. Newest at the bottom.

**D-001 Repo root is the `cob-agent/` root from PLAN.md.** The repo is `cobmis`; the plan's layout is applied directly at the root rather than nesting another folder.

**D-002 Postgres runs on host port 5433, not 5432.** Avoids clashing with a native Postgres many dev machines already run. Configurable via `POSTGRES_PORT`. Bound to `127.0.0.1` only.

**D-003 Three DB login roles created at init (`db/init/10_roles.sh`).**
`cob_app` (API), `cob_ingest` (only writer of corpus/structured data), `cob_readonly` (text-to-SQL executor). `cob_readonly` has `default_transaction_read_only=on`, `statement_timeout=10s`, and USAGE on `public_data` only; no access to `private` until a collection grant is issued. Per-table grants come with the migrations that create tables. Defence in depth on top of sqlglot validation (Phase 4).

**D-004 Model tags are placeholders until verified on the target host.** *(Updated 2026-10-21: the owner confirmed `qwen3.5:9b` (Q4_K_M, 6.6 GB) and `bge-m3` are pulled and working on the target machine; the `small` profile now uses exactly those. Medium/large tags remain unverified.)* The build sandbox cannot reach `registry.ollama.ai` or `ollama.com`, so tags in `config.yaml` (`qwen3.5:9b`, `qwen3.6:27b`, `qwen3.6:35b-a3b`, `qwen3-coder:30b`, `bge-m3`, `nomic-embed-text`) come from 2026 secondary sources and must be confirmed with `ollama pull` + `scripts/check_ollama.py` before Phase 3. Final choice waits on the owner's hardware specs.

**D-005 `think: false` on every chat call.** Qwen 3.x tags enable thinking by default. For routing, SQL generation and grounded answers we want short deterministic output; thinking adds latency and can leak reasoning into answers. Revisit per-task if SQL accuracy benefits from it.

**D-006 Loopback is enforced in code, not just in config.** `Settings` refuses to start if the Ollama URL, DB host or API host is not a loopback address. This makes "accidentally pointed at a remote/hosted endpoint" a startup error. The Ollama HTTP client also sets `trust_env=False` so a system `HTTP(S)_PROXY` can never relay model traffic off-box.

**D-007 Separate `content` schema for documents and chunks.** The plan lists `documents`/`chunks` without a schema. Keeping them out of `public_data` means the text-to-SQL role never sees raw chunk text (including restricted-collection chunks) by schema permission alone.

**D-008 Embedding dimension is checked on every embed call.** A mismatch between the model and `embed_dim` (and so the `vector(N)` column) fails loudly instead of corrupting the index.

**D-009 `/health` returns 503 when degraded.** Lets scripts and the admin UI treat it as a readiness probe.

**D-010 `num_ctx` on every Ollama call, `think: false` on every chat call (owner requirement).** Ollama's default context window (4K on many builds) silently truncates long RAG prompts, so the client always sends `options.num_ctx` from the profile (8192 for `small`), including on `/api/embed`. A unit test inspects every request payload to enforce both settings.

**D-011 Scraper design (Phase 1).**
- *Not yet validated against the live page.* Neither the build sandbox's proxy nor the web-fetch tool could reach `cob.go.ke` during Phase 1, so the listing parser is built from the structure described in PLAN.md §1 and common WordPress Download Manager markup, and tested against a clearly labelled synthetic fixture. Every live run saves `data/listing_snapshot.html`; that snapshot should replace the synthetic fixture as the regression test.
- Listing parsing is layout-tolerant: any same-site `/download/<slug>/` link inside the main content, with the FY taken from the title, then the slug, then the nearest FY heading above it (noted in `parse_notes`).
- Periods are cumulative in COB reports, so "Third Quarter" maps to `9M` and "Fourth Quarter" to `FY`. Unrecognised periods are kept as `null` with a note, not guessed.
- A `/download/<slug>/` URL that redirects straight to a PDF is saved from that first response (one request). Otherwise the package page is searched for `data-downloadurl`, `?wpdmdl=`, `.pdf` hrefs and `onclick` URLs. Playwright is not used unless a live run shows plain HTTP is not enough.
- Status meanings: `unavailable` = listed as 0 KB with no file, HTTP 404/410, or no file link (skipped on reruns unless `--retry-unavailable`); `failed` = transient or bad content (retried on every rerun); `scanned` = downloaded but <50% of sampled pages have a text layer (OCR in Phase 2).
- robots.txt: honoured, including `Crawl-delay`. A 401/403 robots.txt is treated as "disallow all" (Python stdlib convention); an unreachable one falls back to the 2–3 s delay.
- Downloads are written unbuffered to `.part` so an interrupted transfer keeps what arrived and resumes with HTTP `Range`.

**D-012 PyMuPDF (AGPL-3.0) is acceptable.** This is a private, non-distributed deployment. If the system is ever offered as a network service to third parties, revisit (AGPL network clause) or swap in pdfplumber/pypdfium2.

# COBMIS — Proof of Concept Overview

**Audience:** client stakeholders, product owners, and engineers joining the project.  
**Goal of this document:** explain *what we are building*, *why the architecture looks this way*, *what works today*, and *what arrives with more time and compute*.

---

## 1. One-sentence pitch

**A private, on-premises assistant that answers questions about Kenyan county budget implementation using the Controller of Budget’s official reports — with citations you can verify, and without sending data to any cloud AI provider.**

---

## 2. The problem we solve

County budget implementation review reports are large (hundreds of pages), table-heavy, and updated every quarter. Analysts and oversight users need answers like:

- “What challenges did Kericho report in the first nine months?”
- “What was Nairobi’s pending bills position?”
- “Do we even have FY 2022/23 loaded?”

Today that means searching PDFs by hand. Generic chatbots either **cannot see the PDFs**, or — if you paste content into a hosted API — **send sensitive fiscal material off-premises** and still often invent numbers.

COBMIS keeps the documents and the models **on the client’s machine (or a machine they control)**.

---

## 3. What “local AI” means here (Ollama)

### What Ollama is

[Ollama](https://ollama.com) is a **local model runner**. You download open-weight models once; afterward they run as a service on `127.0.0.1` (this computer only). The application talks to Ollama the way it might talk to an API — except the “API” never leaves the box.

We use Ollama so that:

| Requirement | How Ollama helps |
|---|---|
| No paid LLM invoices | Models are open weights; inference is local |
| Privacy by construction | Traffic stays on loopback; app refuses remote endpoints |
| Offline-capable demo | After models + PDFs are present, network can be cut |
| Hardware flexibility | Same code; swap model size via `COB_PROFILE` |

**Ollama is not the product.** It is the engine room. The product is the pipeline: scrape → parse → validate → retrieve/SQL → cited answer.

### The two (sometimes three) models

| Role | Model on `small` (current PoC laptop) | What it does |
|---|---|---|
| **Chat** | `qwen3.5:9b` | Reads retrieved PDF excerpts and writes a short answer with `[S1]`-style citations. Must not invent figures. |
| **Embeddings** | `bge-m3` | Turns each text chunk (and each question) into a 1024-number vector so we can find “similar meaning” passages quickly. |
| **SQL** | same as chat on `small`; a coder model on larger profiles | Writes `SELECT` queries against validated chapter-2 tables (`v_county_metrics`). |

**Why two models today?**  
Finding the right page is a *search* problem (embeddings + keywords). Writing a careful answer is a *language* problem (chat). Using one small chat model for both search and writing would be weaker and slower; embeddings are specialised for similarity search and are cheap to run in batches during ingest.

**Why not ChatGPT / Claude API?**  
Those would send report text (or embeddings of it) to a third party. That conflicts with the hard requirement: *private by construction*.

---

## 4. Why we download the COB data

Answers must come from **the same PDFs the Controller of Budget publishes**, not from the model’s training memory.

1. **Scrape** `cob.go.ke` → PDFs in `data/raw/`, inventory in `data/manifest.json`.  
2. **Parse** → text, headings, tables, county sections.  
3. **Validate & load** chapter-2 (all-county) tables into Postgres with quarantine for bad rows.  
4. **Chunk & embed** narrative and per-county tables for retrieval.  
5. **Ask** → route to catalog / SQL / RAG / both → answer with provenance.

If a year was never downloaded, the system must say **“No”** — not guess. That is already how “Do you have FY 2022/23?” works when only FY 2025/26 is loaded.

The PoC often uses **`--limit 3`** (three newest reports) so a laptop can finish ingest in a reasonable time. Expanding to the full archive is a scrape + ingest re-run, not a redesign.

---

## 5. Capabilities today (MVP / PoC)

What you can show in a demo **now**:

| Capability | Example | How it works |
|---|---|---|
| **Catalog / meta** | “What data do you have?”, “Do you have FY 2022/23?”, “What can I ask?” | Pure SQL + templates. No model inventing coverage. |
| **Narrative Q&A with citations** | “What challenges did Kericho face in the first nine months…?” | Hybrid search (vector + full-text) → local chat over cited chunks. |
| **County-strict retrieval** | Kericho challenges → Kericho §3.12.19 (pp. 297–298), not national “challenges” chapters | Retrieval fix: county pool first; national chapters only as labelled fallback. |
| **Honest refusal** | Year or slice not ingested → clear “not in the loaded reports” | Filters + empty result, not hallucination. |
| **Structured facts in DB** | Own-source revenue, absorption, pending bills, etc. for 47 counties | Loaded and validated into `public_data`. |
| **Text-to-SQL (numeric Q&A)** | “Top 5 counties by pending bills?”, absorption / OSR lookups | Local model writes SQL → sqlglot validate → `cob_readonly` execute → narrate only from rows (“How I got this” shows SQL + table). |
| **Hybrid `both` answers** | “Kericho challenges and OSR performance…?” | SQL owns money/%; RAG owns challenges; one merge pass; dual citations. |
| **Follow-up rewrite** | “and what about Kilifi?” | Standalone rewrite in CLI (`--prior`) and in the browser chat thread. |
| **Evidence Terminal UI** | Login → Ask / Library / About / Admin at `127.0.0.1:3000` | Three-pane workspace: answer + Sources + How I got this; PDF open in Library. |
| **Privacy posture** | Loopback-only services; no hosted LLM | Enforceable in config + code. |
| **Quality tooling** | `table_qa` HTML side-by-side with PDF pages | Builds trust in extraction. |

Catalog in the CLI and the **About this data** page share one SQL payload. Numeric answers come from the database; narrative from cited PDF chunks; hybrid questions get both — now in the browser as well as the terminal.

---

## 6. What is not ready yet (honest scope)

| Gap | Impact | When |
|---|---|---|
| **Restricted private uploads** | Login exists; full private-collection ACL + upload UX still to harden. | Phase 7 |
| **LLM router (optional)** | Heuristics cover PoC routes; model-based classify later if needed. | Later |
| **Full historical archive** | PoC may only have FY 2025/26 until a full scrape. | Ops / Phase 1 re-run |
| **OCR** | Image-only pages skipped until OCR is enabled. | Deferred (D-021) |
| **Larger models** | Better writing/SQL on medium/large profiles needs more VRAM/RAM. | Hardware upgrade |
| **Offline harden checklist** | Backup/restore + formal offline test ritual. | Phase 8 |

This honesty is part of the sell: the PoC proves local stack, extraction, retrieval discipline, refusals, hybrid answers, and a browser evidence desk.

---

## 7. What more time and computing power buy you

| Investment | Outcome |
|---|---|
| **More disk + overnight scrape/ingest** | All available FYs/periods; answers span 2012/13–2025/26 as published. |
| **Bigger GPU / more RAM (`medium` / `large`)** | Stronger chat and a dedicated SQL coder model; longer context; optional reranker. |
| **Phase 7** | Restricted collections for internal memos/spreadsheets that never appear in public catalog counts. |

The architecture does not change when you scale hardware: same Postgres, same Ollama, larger tags in `config.yaml`.

---

## 8. Architecture (simple picture)

```
  User question
       │
       ▼
  Router (catalog | sql | rag | both)
       │
       ├─ catalog ──► SQL over documents / facts ──► templated answer
       ├─ sql     ──► text-to-SQL → cob_readonly → narrate from rows
       ├─ rag     ──► embed → hybrid search → chat over cited chunks
       └─ both    ──► SQL + RAG in sequence → merge (SQL figures + RAG prose)
       │
       ▼
  Answer + sources (RAG pages and/or SQL “How I got this”)
```

Everything above runs on **localhost**. After the initial download of models and PDFs, the network is optional.

---

## 9. Suggested 10-minute demo script

1. Open **http://127.0.0.1:3000** — sign in (`admin@local` after `seed_admin`).  
2. **About** — coverage matches what is on this machine.  
3. **Ask:** pending bills top-5 → route `sql` → **How I got this**.  
4. **Ask:** Kericho challenges → route `rag` → citation chips → Sources.  
5. **Ask:** challenges + OSR → route `both` → figures from SQL, narrative from PDF.  
6. **Library** — open a report PDF.  
7. Optional CLI still works; Admin shows health/audit.  
8. Close with roadmap: private uploads, full archive.

---

## 10. Risks we already design around

| Risk | Mitigation in the PoC |
|---|---|
| Model invents numbers | Citations required; unsupported-number warning; catalog/SQL paths don’t use model memory for counts |
| Wrong county’s chapter | Strict county retrieval; national chapters capped and labelled |
| Bad table extraction | Row/table validation, quarantine, visual QA |
| Accidental cloud leak | Loopback enforcement; no hosted AI dependencies |
| Weak laptop | `small` profile still demos the story; upgrade path documented |

---

## 11. How to talk about this to a buyer

**Do say:**

- “All inference and documents stay on infrastructure you control.”  
- “Every narrative claim is tied to a report page you can open.”  
- “If the year isn’t loaded, it says no — it doesn’t invent an archive.”  
- “We’ve already turned the hardest PDF tables into queryable, validated data for 47 counties.”  
- “Ask for a ranking — you get the database. Ask for challenges — you get the PDF narrative. Ask for both — you get figures from SQL and explanation from the report, in one answer, with both kinds of proof.”  
- “The web UI is the next product increment on the same foundation.”

**Don’t over-claim:**

- Don’t promise ChatGPT-level fluency on a 9B local model without showing citations.  
- Don’t imply every historical year is loaded until scrape/ingest says so.  
- Don’t imply private document upload is finished — ACL is stubbed for the next phase.

---

## 12. Where to go next in the repo

| Need | Open |
|---|---|
| Restart on a new PC | [`README.md`](../README.md) |
| Engineering plan | [`PLAN.md`](../PLAN.md) |
| Why we chose X | [`DECISIONS.md`](../DECISIONS.md) |
| Eval goldens + `eval_run` | [`eval/golden_questions.yaml`](../eval/golden_questions.yaml) |

---

*Document version: aligned with Phase 6 Evidence Terminal UI (D-027), Phase 5 both-merge (D-026), and FY 2025/26 three-report PoC corpus.*

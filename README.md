# Patent Hunter

> An autonomous agent that scans newly-expired US utility patents and scores their commercial viability with two independent LLMs.

Inspired by Gipp ([@gippp69](https://x.com/gippp69))'s observation that **4.2M+ expired US utility patents** are sitting in the public domain as complete manufacturing manuals, waiting for someone to read them. This repo is an open-source experiment in turning that observation into a reproducible pipeline.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue)
![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue)

---

## What it does

Every week:

1. Pull granted patents for the target ISO week from PatentsView (deterministic).
2. Filter by CPC categories (`kitchen`, `pet_products`, `cable_management`, `household`) and an approximate "likely lapsed" rule (deterministic).
3. Score the candidates in parallel with **Anthropic Sonnet** and **OpenAI Codex** — two independent judges (LLM only here).
4. Adopt the rows where **both models** return a score >= 7.
5. Emit `report.html`, `scores.jsonl`, `run.log` under `out/<ISO-week>/`.

Roughly 90% deterministic Python, 10% LLM. The LLM is asked only for the commercial-viability judgement; everything around it (fetch, filter, formatting, IO) is plain Python.

---

## Architecture

```
+----------------------------------------------------------------+
|  Hono Edge API (Node + TypeScript)                             |
|    GET  /api/patents/top         latest adopted top-N          |
|    GET  /api/eval/latest         latest eval metrics           |
|    POST /api/eval/run            kick off run_eval.py          |
|    POST /api/scoring/run         kick off dryrun.py            |
|    GET  /api/scoring/stream/...  SSE tail of run.log           |
|    POST /api/discord/interactions (Ed25519 verified)           |
+---------------------+------------------------------------------+
                      | subprocess (fire and forget)
                      v
+----------------------------------------------------------------+
|  Python pipeline                                                |
|    P1   CLI:        patent_hunter.cli                           |
|    P1.5 Eval:       evals/run_eval.py                           |
|    P2   StateGraph: patent_hunter.graph (LangGraph)            |
+----------------------------------------------------------------+
```

### LangGraph topology

```mermaid
graph TD;
    __start__([START]) --> fetch;
    fetch --> score_sonnet;
    fetch --> score_codex;
    score_sonnet --> verify;
    score_codex --> verify;
    verify --> report;
    report --> __end__([END]);
```

| Node | Responsibility |
|---|---|
| `fetch` | PatentsView fetch; dryrun/tests inject the fixture from `scripts/dryrun.py` |
| `score_sonnet` | `scorers.sonnet.score_batch` (Anthropic SDK) |
| `score_codex` | `scorers.codex.score_batch` (Codex CLI via subprocess) |
| `verify` | Adopt where `sonnet.score >= threshold` AND `codex.score >= threshold` |
| `report` | Same `runner.write_outputs` as P1 (`scores.jsonl` / `run.log` / `report.html`) |

`score_sonnet` and `score_codex` fan out in parallel from `fetch`. Codex is **not** shown Sonnet's score — each judge is independent (Clean Context for Verifier). Checkpoint: `MemorySaver` with `thread_id = patent-hunter:p2:<ISO-week>` (deterministic).

---

## Roadmap

| Phase | Scope | Status |
|:-:|---|:-:|
| **P1**   | Python CLI: fetch + filter + dual-model scoring + HTML/JSONL output | ✅ |
| **P1.5** | Eval harness (Plan/Build/Review): golden dataset + 4 metrics + HTML report | ✅ |
| **P2**   | LangGraph StateGraph (MemorySaver checkpoint, parallel fan-out) | ✅ |
| **P2.5** | Hono Edge API (TypeScript, Node + Cloudflare Workers compatible) | ✅ |
| **P3**   | Discord bot notifications + Approval Gate (LangGraph `interrupt` / `resume`) | ☐ |
| **P4**   | Postgres checkpoint, Alibaba RFQ poller (dynamic backoff) | ☐ |
| **P5**   | Amazon SP-API listing draft generator | ☐ |

---

## Quick start

### Python pipeline

```bash
# Install (uv recommended; pip also works)
uv sync --extra dev
# or: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Provide credentials
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY=sk-ant-...

# Dry run (no network, no API keys needed)
python3 scripts/dryrun.py

# Production run (previous ISO week)
python3 -m patent_hunter run

# Specify a week
python3 -m patent_hunter run --week 2026-W19 --top-n 10

# Run via LangGraph instead of the linear CLI
.venv/bin/python -m patent_hunter.graph.cli --week 2026-W19 --dryrun

# Tests (38 + 4 = 42)
python3 scripts/run_checks.py
```

Outputs land under `out/<ISO-week>/`:
- `report.html` — Bootstrap CDN, one-page summary
- `scores.jsonl` — every patent × every model (debugging)
- `run.log` — token counts, estimated cost, wall time

### Hono Edge API

```bash
cd web
npm install
npm run dev    # http://localhost:8787
```

```bash
# Sample requests
curl http://localhost:8787/
curl "http://localhost:8787/api/patents/top?n=3"
curl http://localhost:8787/api/eval/latest
```

Compatible with Cloudflare Workers: routes use only Web standard `Request`/`Response`.

---

## Measured cost (dry run, 4 fixture patents)

```
fetched=4 scored=4 adopted=3
sonnet_input_tokens=1500 output_tokens=600 cost=$0.0135
codex_invocations=1 cost_estimate=$0.30
total_cost=$0.3135
```

Extrapolated: ~$1 per 50-patent batch, well under $5 per weekly run for 100 candidates.

---

## Design choices

| Topic | Choice | Rationale |
|---|---|---|
| Data source | PatentsView v1 REST API | XML bulk is too heavy for P1; BigQuery requires a GCP account. REST is the lowest-friction option. |
| "Expired" approximation | `grant_date` ~12 years old + utility + small assignee | The maintenance-fee event endpoint is not exposed by PatentsView v1. The LLM acts as a downstream precision filter; the deterministic rule is tuned for recall. |
| CPC filter | `fetchers/categories.py` (A47J/A47G/B65D/F24C/A01K/H02G/F16L/H01R/A47B/A47C/A47L/B25H) | Prefix match catches a wide set; the prompt's REJECT clauses drop chemicals/software. |
| Prompt | Gipp-style 6 fields + JSON-only output | Smaller models behave better with the explicit JSON schema reminder. |
| Batch size | 50 patents, two models in parallel (`asyncio.gather`); batches are sequential | Avoids rate-limit blow-up while still amortising request overhead. |
| Adoption rule | Both models score >= threshold (default 7) | Each model scores independently; Codex never sees Sonnet's output. |

---

## Eval harness (P1.5)

A continuous-evaluation layer using the **Plan / Build / Review** sequence and Anthropic's 4-stage maturity model.

```
evals/
+-- cases.json        # Golden dataset (expected_score_range + expected_status + notes)
+-- run_eval.py       # 3 repeats + metric computation + HTML report
+-- out/eval_<ts>/    # metrics.json + report.html per run
```

Four metrics:

| Metric | Meaning |
|---|---|
| **Agreement rate** | Share of patents where Sonnet and Codex returned the same adopt/reject verdict |
| **In-range rate** | Share of patents whose mean consensus score fell inside `expected_score_range` |
| **Status match rate** | Share of patents whose final status matched `expected_status` (proxy for precision/recall) |
| **Avg score sigma** | Standard deviation across 3 repeats (reproducibility) |

```bash
python evals/run_eval.py
```

4-stage maturity self-assessment:

| Stage | Capability | Status |
|:-:|---|:-:|
| 1 | Smoke test (it runs) | ✅ |
| 2 | Golden dataset | ✅ |
| 3 | Regression detection (alert on drift) | ✅ |
| 4 | Continuous eval (CI integration) | planned for P2.5 |

> Note: the dry-run scorers are deterministic stubs, so `avg_score_sigma = 0` for the demo. The harness was designed so that swapping in the live API keys is a one-line client injection — `cases.json` and `run_eval.py` stay unchanged.

---

## Non-goals (deliberately out of scope)

- A multi-tenant SaaS for other users (this is a single-operator tool).
- Patent databases other than USPTO (JPO/EPO might come later).
- Monetisation paths other than physical products (no lead-gen, no affiliate).
- Auto-purchase or auto-listing (every commitment step keeps a human-in-the-loop gate).
- Feature parity with PatentInspiration / Helium 10.

---

## Known limitations of P1

- The "lapsed" filter is approximate — `grant_date ~ 12 years old` is a vintage heuristic, not a real maintenance-fee check. P3 plans to wire in the USPTO PEDS endpoint.
- Only `title + abstract + first claim` are fetched. Full claims and figures are deferred to P3 (cached through the Hono edge).
- Codex cost is currently estimated as a flat $0.30 per batch; switching to billing-log parsing is on the P2.5 list.
- The report is a single static HTML page; live filtering / sorting is intentionally out of scope.

---

## Repository layout

```
patent_hunter/
+-- src/patent_hunter/
|   +-- fetchers/         PatentsView fetcher + CPC categories
|   +-- scorers/          Sonnet/Codex scorers, prompts, JSON extraction
|   +-- graph/            LangGraph state, nodes, builder, CLI, compat layer
|   +-- io/               Report renderer + packaged Jinja2 templates
|   +-- notifications/    P3 notification Protocol placeholder
|   +-- checkpointers/    P4 checkpoint Protocol placeholder
|   +-- cli.py            Linear CLI
|   +-- runner.py         Linear pipeline orchestration
|   +-- models.py         Shared dataclasses
|   +-- week.py           ISO week helpers
+-- tests/                pytest (42 tests, all mocked; mirrors packages)
+-- evals/                Eval harness (P1.5)
+-- web/                  Hono Edge API (P2.5)
+-- scripts/              dryrun.py, run_checks.py
+-- pyproject.toml
+-- CONTRIBUTING.md
+-- LICENSE
```

---

## Acknowledgements

- **Gipp** ([@gippp69](https://x.com/gippp69)) — for publicising the "expired patents are open-source manufacturing manuals" framing and the original scoring prompt.
- **USPTO** and **PatentsView** — for keeping the underlying dataset open.
- **Anthropic** and **OpenAI** — for the SDKs.
- **LangGraph** — for making the StateGraph + Checkpoint pattern this approachable.
- **Hono** — for being delightful to write edge APIs in.

## License

MIT — see [LICENSE](LICENSE).

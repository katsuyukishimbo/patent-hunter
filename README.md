# Patent Hunter

> Find commercially viable expired US patents — automatically, with two LLMs cross-checking each other.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue)
![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue)
![Tests](https://img.shields.io/badge/tests-42%20passing-brightgreen)

There are **4.2M+ expired US utility patents** sitting in the public domain. Each one is essentially a free manufacturing manual — dimensions, tolerances, materials, assembly — written when someone thought it was worth $15,000 in legal fees to protect. Nobody re-reads them. Patent Hunter does.

Every week, it pulls the latest grants, filters by category, and asks **Anthropic Sonnet** and **OpenAI Codex** — independently — whether the underlying invention is commercially viable today. Only the rows where both judges agree are surfaced.

## Features

- **Dual independent scoring** with two LLMs from different providers. Codex never sees Sonnet's score (Clean Context for Verifier).
- **~90% deterministic / ~10% LLM.** Fetch, filter, formatting and IO are plain Python; the LLM is only asked for the commercial-viability judgement.
- **Continuous evaluation harness** with a golden dataset and four metrics (agreement, in-range, status-match, sigma).
- **LangGraph orchestration** with `MemorySaver` checkpoint and parallel fan-out of the two scorers.
- **Edge API in Hono** for triggering runs, streaming logs (SSE), and accepting Discord interactions (Ed25519 verified).
- **One HTML report per week** under `out/<ISO-week>/` — patent links, BOM estimate, Amazon gap analysis, and consensus score.

## Quickstart

```bash
# Clone and install (Python 3.11+)
git clone https://github.com/katsuyukishimbo/patent-hunter.git
cd patent-hunter
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Try it without any API key (uses fixture data)
python3 scripts/dryrun.py
open out/2026-W19/report.html
```

That's the full loop. The dry run uses stubbed scorers, so no spend, no network.

## Usage

```bash
# Set credentials for live scoring
cp .env.example .env       # then edit: ANTHROPIC_API_KEY=sk-ant-...

# Score the previous ISO week
python3 -m patent_hunter run

# Score a specific week, top 10 results
python3 -m patent_hunter run --week 2026-W19 --top-n 10

# Run via LangGraph instead of the linear CLI
.venv/bin/python -m patent_hunter.graph.cli --week 2026-W19 --dryrun

# Evaluate the pipeline against the golden dataset
python3 evals/run_eval.py
```

Outputs land under `out/<ISO-week>/`:

```
report.html     one-page summary, opens in any browser
scores.jsonl    every patent x every model (debugging)
run.log         token counts, estimated cost, wall time
```

Sample CLI output:

```
fetched=4 scored=4 adopted=3
sonnet_input_tokens=1500 output_tokens=600 cost=$0.0135
codex_invocations=1 cost_estimate=$0.30
total_cost=$0.3135
```

A real weekly run on ~100 candidates costs well under **$5**.

### Edge API

The repo also ships a Hono server that wraps the Python pipeline as HTTP endpoints (compatible with Cloudflare Workers):

```bash
cd web
npm install
npm run dev
# -> http://localhost:8787

curl http://localhost:8787/api/patents/top?n=3
curl http://localhost:8787/api/eval/latest
```

Endpoints: `GET /api/patents/top`, `GET /api/eval/latest`, `POST /api/eval/run`, `POST /api/scoring/run`, `GET /api/scoring/stream/:week` (SSE), `POST /api/discord/interactions`.

## How it works

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
| `fetch` | PatentsView REST query, then a deterministic "likely lapsed" filter. |
| `score_sonnet` | Anthropic SDK, model `claude-sonnet-4-6`. |
| `score_codex` | OpenAI Codex via subprocess. Does **not** see Sonnet's score. |
| `verify` | Adopt only where `sonnet.score >= 7` **and** `codex.score >= 7`. |
| `report` | Emit `report.html` + `scores.jsonl` + `run.log`. |

## Roadmap

| Status | Capability |
|:-:|---|
| ✅ | Python CLI (fetch + dual-model scoring + HTML report) |
| ✅ | Evaluation harness (golden dataset, 4 metrics, regression detection) |
| ✅ | LangGraph orchestration (parallel fan-out, `MemorySaver` checkpoint) |
| ✅ | Hono Edge API (REST + SSE + Discord webhook) |
| ☐ | Discord approval gate (`interrupt` / `resume` via LangGraph) |
| ☐ | Postgres checkpoint, dynamic-backoff polling, Alibaba RFQ |
| ☐ | Amazon SP-API listing draft generator |

<details>
<summary><strong>Key design choices</strong></summary>

| Topic | Choice | Why |
|---|---|---|
| Data source | PatentsView v1 REST API | XML bulk is heavy; BigQuery requires a GCP account. REST is the lowest-friction option. |
| "Expired" approximation | `grant_date` ~12 years old + utility + small assignee | Maintenance-fee events aren't exposed by PatentsView v1. The LLM acts as the downstream precision filter; the deterministic rule is tuned for recall. |
| Adoption rule | Both models must score ≥ 7 | Cuts false positives. Codex is never shown Sonnet's score. |
| Batch size | 50 patents, two models in parallel (`asyncio.gather`); batches are sequential | Amortises request overhead without tripping rate limits. |
| Prompt | Gipp-style six fields + JSON-only output | Smaller models behave better with the explicit JSON schema reminder. |

</details>

<details>
<summary><strong>Known limitations</strong></summary>

- The "lapsed" filter is an approximation, not a real maintenance-fee check.
- Only `title + abstract + first_claim` are fetched. Full claims and figures will arrive with the Edge API caching layer.
- Codex cost is currently estimated as a flat $0.30 per batch; billing-log parsing is on the next milestone.
- The report is a single static HTML page — live filtering / sorting is intentionally out of scope.

</details>

## Contributing

PRs and issues welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test commands, and the sub-package layout.

## Acknowledgements

- **Gipp** ([@gippp69](https://x.com/gippp69)) for publicising the framing of expired patents as open-source manufacturing manuals.
- **USPTO** and **PatentsView** for keeping the underlying dataset open.
- **Anthropic**, **OpenAI**, **LangGraph**, and **Hono** for the SDKs and runtimes this is built on.

## License

[MIT](LICENSE)

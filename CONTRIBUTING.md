# Contributing

## Development Setup

Use `uv` when available:

```bash
uv sync --extra dev
```

The plain `venv` path is also supported:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Copy `.env.example` to `.env` when running live scoring. Dry runs and tests do
not need API keys.

## Tests

Run the Python suite through the repository wrapper:

```bash
python3 scripts/run_checks.py
```

The wrapper runs pytest with the local `src/` package and `.venv` site-packages
on `sys.path`.

## Hono Edge API

```bash
cd web
npm install
npm run dev
```

The local server listens on `http://localhost:8787`. Type-check with:

```bash
npm run typecheck
```

## Sub-package Responsibilities

- `fetchers/`: patent and marketplace candidate fetchers. `bigquery.py` is the current production source; `legacy_patentsview.py` is preserved for reference (see [ADR-001](docs/adr/ADR-001-data-source-bigquery.md)); `base.py` defines the fetcher Protocol.
- `scorers/`: independent LLM scoring adapters. `sonnet.py` (Claude CLI subprocess) and `codex.py` (Codex CLI subprocess) share prompts and JSON extraction. See [ADR-002](docs/adr/ADR-002-claude-cli-cwd-tmp.md) for the `cwd=/tmp` requirement and [ADR-003](docs/adr/ADR-003-dual-independent-scoring.md) for the Clean Context for Verifier pattern.
- `graph/`: LangGraph orchestration only. Business logic stays in fetchers, scorers, and runner IO. Node kwarg naming uses `gr=` (see [ADR-004](docs/adr/ADR-004-langgraph-gr-parameter.md)).
- `io/`: report rendering and packaged templates.
- `notifications/`: Discord webhook (Phase 2). Future Approval Gate logic lands here in Phase 3.
- `checkpointers/`: durable checkpoint Protocol placeholder (Phase 4 Postgres backend).
- `observability/`: append-only `events.jsonl` writer used by every layer.

## Architecture Decision Records

Major design judgements are recorded under [`docs/adr/`](docs/adr/).
When you make a non-obvious change — a new external service, a
counter-intuitive default, a workaround for a framework reserved
keyword — add an ADR before merging. Use the [template](docs/adr/ADR-template.md).

## PR Template

```markdown
## Summary
- 

## Tests
- [ ] python3 scripts/run_checks.py
- [ ] .venv/bin/python -m patent_hunter run --help
- [ ] .venv/bin/python -m patent_hunter.graph.cli --week 2026-W19 --dryrun
- [ ] python3 evals/run_eval.py
- [ ] cd web && npm run typecheck

## Notes
- 
```

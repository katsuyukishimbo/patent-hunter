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

- `fetchers/`: patent and marketplace candidate fetchers. `patentsview.py` is the current P1 source; `base.py` defines the future fetcher Protocol.
- `scorers/`: independent LLM scoring adapters. `sonnet.py` and `codex.py` share prompts and JSON extraction helpers.
- `graph/`: LangGraph orchestration only. Business logic stays in fetchers, scorers, and runner IO.
- `io/`: report rendering and packaged templates.
- `notifications/`: P3 Discord/Approval Gate Protocol placeholder.
- `checkpointers/`: P4 durable checkpoint Protocol placeholder.

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

# Architecture Decision Records

This directory captures the non-obvious design judgements made while
building Patent Hunter. Each ADR follows Michael Nygard's classic
four-section layout (**Context / Decision / Status / Consequences**)
plus "Alternatives considered" and "References".

The intent is that a future reader (a hiring manager, a contributor,
or future-me) can answer "why does the code look like this" without
having to reconstruct the original constraints from scratch.

## Index

| # | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-data-source-bigquery.md) | Google Patents BigQuery as the patent data source | Accepted |
| [ADR-002](ADR-002-claude-cli-cwd-tmp.md) | Claude CLI subprocess invoked with `cwd=/tmp` | Accepted |
| [ADR-003](ADR-003-dual-independent-scoring.md) | Dual independent scoring with Clean Context for Verifier | Accepted |
| [ADR-004](ADR-004-langgraph-gr-parameter.md) | Graph nodes accept config via `gr=` (not `runtime=`) | Accepted |

(More ADRs will be added as new decisions are made.)

## When to write an ADR

Add an ADR when a decision is:

1. **Non-obvious from the code** — somebody reading the diff would ask
   "why on earth did they do it this way?".
2. **Reversible at high cost** — flipping the choice later means a
   noticeable refactor, dependency change, or behavioural shift.
3. **Driven by an external constraint** — a third-party service
   limitation, a research paper, an authentication boundary, etc.

If the decision is trivially recoverable (a config default, a CLI
flag wording) it doesn't need an ADR. A code comment or a commit
message is enough.

## Template

See [ADR-template.md](ADR-template.md).

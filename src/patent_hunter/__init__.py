"""Patent Hunter Phase 1 CLI package.

Pipeline:
  1. Fetch granted patents for the target ISO week from BigQuery (deterministic).
  2. Filter by CPC categories and approximate "likely lapsed" rule (deterministic).
  3. Score in parallel with Claude Code CLI and OpenAI Codex (LLM judgement only).
  4. Adopt rows where BOTH models return score >= threshold.
  5. Emit report.html, scores.jsonl, run.log under out/<ISO-week>/.

Design principles:
  - Roughly 90% deterministic + 10% LLM. The LLM is asked only for the
    commercial-viability judgement; everything around it (fetch, filter,
    formatting, IO) is plain Python.
  - Clean Context for Verifier: Codex is NOT shown the Sonnet-role score from
    Claude Code CLI. Each model scores independently and the runner adopts only
    when both agree.
"""

from .models import Patent, RunStats, ScoredPatent, ScoreResult
from .runner import RunConfig, run, write_outputs
from .week import IsoWeek, previous_iso_week

__version__ = "0.1.0"

__all__ = [
    "IsoWeek",
    "Patent",
    "RunConfig",
    "RunStats",
    "ScoreResult",
    "ScoredPatent",
    "previous_iso_week",
    "run",
    "write_outputs",
]

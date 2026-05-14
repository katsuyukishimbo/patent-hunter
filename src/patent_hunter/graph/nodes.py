"""LangGraph node functions for the Patent Hunter pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from patent_hunter.fetchers import FetchConfig, fetch_patents
from patent_hunter.models import Patent, RunStats
from patent_hunter.observability import configure_events, emit
from patent_hunter.runner import (
    AllScoringFailedError,
    CostBudgetExceededError,
    RunConfig,
    _merge_scored,
    _notify_top_patents,
    _raise_if_all_scoring_failed,
    _record_partial_failure_warning,
    _update_scored_stats,
    write_outputs,
)
from patent_hunter.scorers import codex as scorer_codex
from patent_hunter.scorers import sonnet as scorer_sonnet
from patent_hunter.week import parse_iso_week, utcnow_iso

from .state import PatentHunterState


@dataclass(frozen=True)
class GraphRuntime:
    """Runtime knobs captured by graph nodes instead of stored in checkpoints."""

    out_dir: Path = Path("out")
    score_threshold: int = 7
    max_per_category: int = 25
    vintage_years: int = 12
    top_n: int = 10
    max_cost_usd: float = 10.0
    fetched_patents: list[Patent] | None = None
    sonnet_client: Any | None = None
    codex_runner: Callable[..., Any] | None = None
    discord_webhook_url: str | None = None


async def fetch_node(
    state: PatentHunterState, *, gr: GraphRuntime | None = None
) -> PatentHunterState:
    """Fetch candidate patents, using injected fixtures for dryrun/tests.

    The fetcher module export points at the current production data source.

    Note: parameter is named ``gr`` (not ``runtime``) because LangGraph 1.x
    reserves ``runtime`` for its own injected ``Runtime`` object and would
    overwrite a ``partial(..., runtime=...)`` bind.
    """

    gr = gr or GraphRuntime()
    configure_events(week=state["week"], out_dir=gr.out_dir)
    emit(
        "run_started",
        week=state["week"],
        budget_max_usd=gr.max_cost_usd,
        max_per_category=gr.max_per_category,
        vintage_years=gr.vintage_years,
    )
    if gr.fetched_patents is not None:
        emit(
            "fetch_started",
            week=state["week"],
            vintage_years=gr.vintage_years,
            max_per_category=gr.max_per_category,
        )
        patents = list(gr.fetched_patents)
        emit("fetch_done", week=state["week"], count=len(patents), duration_ms=0)
    else:
        week = parse_iso_week(state["week"])
        cfg = FetchConfig(
            vintage_years=gr.vintage_years,
            max_per_category=gr.max_per_category,
        )
        patents = await asyncio.to_thread(fetch_patents, week, cfg)
    return {"fetched_patents": patents, "started_at": utcnow_iso()}


async def score_sonnet_node(
    state: PatentHunterState, *, gr: GraphRuntime | None = None
) -> PatentHunterState:
    gr = gr or GraphRuntime()
    out = await scorer_sonnet.score_batch(
        state.get("fetched_patents", []), client=gr.sonnet_client
    )
    return {
        "sonnet_results": out.results,
        "sonnet_input_tokens": out.input_tokens,
        "sonnet_output_tokens": out.output_tokens,
        "sonnet_cost_usd": round(out.cost_usd, 4),
    }


async def score_codex_node(
    state: PatentHunterState, *, gr: GraphRuntime | None = None
) -> PatentHunterState:
    gr = gr or GraphRuntime()
    out = await scorer_codex.score_batch(
        state.get("fetched_patents", []), runner=gr.codex_runner
    )
    return {
        "codex_results": out.results,
        "codex_invocations": out.invocations,
        "codex_cost_usd_estimate": round(out.cost_usd_estimate, 4),
    }


def verify_node(
    state: PatentHunterState, *, gr: GraphRuntime | None = None
) -> PatentHunterState:
    gr = gr or GraphRuntime()
    scored = _merge_scored(
        state.get("fetched_patents", []),
        state.get("sonnet_results", []),
        state.get("codex_results", []),
        score_threshold=gr.score_threshold,
    )
    adopted_patents = [sp for sp in scored if sp.adopted]
    cost_usd = round(
        state.get("sonnet_cost_usd", 0.0) + state.get("codex_cost_usd_estimate", 0.0),
        4,
    )
    sonnet_errors = sum(1 for sp in scored if sp.sonnet.error)
    codex_errors = sum(1 for sp in scored if sp.codex.error)

    budget_warning = cost_usd >= round(gr.max_cost_usd * 0.8, 4)
    if budget_warning:
        emit(
            "budget_warning",
            level="warn",
            week=state["week"],
            budget_max_usd=gr.max_cost_usd,
            total_cost_usd=cost_usd,
            budget_remaining_usd=round(gr.max_cost_usd - cost_usd, 4),
        )
    budget_exceeded = cost_usd > gr.max_cost_usd
    if budget_exceeded:
        emit(
            "budget_exceeded",
            level="error",
            week=state["week"],
            budget_max_usd=gr.max_cost_usd,
            total_cost_usd=cost_usd,
            budget_remaining_usd=round(gr.max_cost_usd - cost_usd, 4),
        )
    return {
        "scored_patents": scored,
        "adopted": adopted_patents,
        "cost_usd": cost_usd,
        "sonnet_errors": sonnet_errors,
        "codex_errors": codex_errors,
        "budget_warning_emitted": budget_warning,
        "budget_exceeded": budget_exceeded,
    }


async def report_node(
    state: PatentHunterState, *, gr: GraphRuntime | None = None
) -> PatentHunterState:
    gr = gr or GraphRuntime()
    week = parse_iso_week(state["week"])
    stats = RunStats(
        week_label=state["week"],
        started_at=state.get("started_at", utcnow_iso()),
        ended_at=utcnow_iso(),
        fetched=len(state.get("fetched_patents", [])),
        after_filter=len(state.get("fetched_patents", [])),
        scored=len(state.get("scored_patents", [])),
        adopted=len(state.get("adopted", [])),
        sonnet_input_tokens=state.get("sonnet_input_tokens", 0),
        sonnet_output_tokens=state.get("sonnet_output_tokens", 0),
        sonnet_cost_usd=state.get("sonnet_cost_usd", 0.0),
        sonnet_errors=state.get("sonnet_errors", 0),
        codex_invocations=state.get("codex_invocations", 0),
        codex_cost_usd_estimate=state.get("codex_cost_usd_estimate", 0.0),
        codex_errors=state.get("codex_errors", 0),
        budget_max_usd=gr.max_cost_usd,
    )
    _record_partial_failure_warning(stats)
    pending_scoring_error: AllScoringFailedError | None = None
    try:
        _raise_if_all_scoring_failed(stats, state.get("scored_patents", []))
    except AllScoringFailedError as exc:
        pending_scoring_error = exc
    if state.get("budget_exceeded", False):
        message = (
            "budget_exceeded: "
            f"total_cost_usd={state.get('cost_usd', 0.0):.4f} "
            f"budget_max_usd={gr.max_cost_usd:.4f}"
        )
        if message not in stats.errors:
            stats.errors.append(message)
    cfg = RunConfig(
        week=week,
        out_dir=gr.out_dir,
        score_threshold=gr.score_threshold,
        max_per_category=gr.max_per_category,
        vintage_years=gr.vintage_years,
        top_n=gr.top_n,
        max_cost_usd=gr.max_cost_usd,
        discord_webhook_url=gr.discord_webhook_url,
    )
    paths = write_outputs(cfg, state.get("scored_patents", []), stats)
    if pending_scoring_error is None and not state.get("budget_exceeded", False):
        await _notify_top_patents(
            webhook_url=gr.discord_webhook_url,
            week_label=state["week"],
            scored=state.get("scored_patents", []),
            top_n=gr.top_n,
        )
    emit(
        "run_done",
        week=stats.week_label,
        adopted=stats.adopted,
        total_cost_usd=stats.total_cost_usd,
        duration_ms=0,
    )
    if pending_scoring_error is not None:
        pending_scoring_error.output_paths = paths
        raise pending_scoring_error
    if state.get("budget_exceeded", False):
        raise CostBudgetExceededError(
            budget_max_usd=gr.max_cost_usd,
            total_cost_usd=state.get("cost_usd", 0.0),
            scored=state.get("scored_patents", []),
            stats=stats,
            output_paths=paths,
        )
    return {"report_paths": {name: str(path) for name, path in paths.items()}}

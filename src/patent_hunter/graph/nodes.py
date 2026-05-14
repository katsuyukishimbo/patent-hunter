"""LangGraph node functions for the Patent Hunter pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from patent_hunter.fetchers import FetchConfig, fetch_patents
from patent_hunter.models import Patent, RunStats, ScoredPatent, ScoreResult
from patent_hunter.runner import RunConfig, _index_by_id, write_outputs
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
    fetched_patents: list[Patent] | None = None
    sonnet_client: Any | None = None
    codex_runner: Callable[..., Any] | None = None


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
    if gr.fetched_patents is not None:
        patents = list(gr.fetched_patents)
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
    sonnet_idx = _index_by_id(state.get("sonnet_results", []))
    codex_idx = _index_by_id(state.get("codex_results", []))

    scored: list[ScoredPatent] = []
    for patent in state.get("fetched_patents", []):
        sonnet = sonnet_idx.get(
            patent.patent_id,
            ScoreResult(patent_id=patent.patent_id, model="sonnet", error="absent"),
        )
        codex = codex_idx.get(
            patent.patent_id,
            ScoreResult(patent_id=patent.patent_id, model="codex", error="absent"),
        )
        consensus = (
            (sonnet.score + codex.score) / 2
            if (sonnet.score and codex.score)
            else 0.0
        )
        adopted = (
            sonnet.error is None
            and codex.error is None
            and sonnet.score >= gr.score_threshold
            and codex.score >= gr.score_threshold
        )
        scored.append(
            ScoredPatent(
                patent=patent,
                sonnet=sonnet,
                codex=codex,
                consensus_score=round(consensus, 2),
                adopted=adopted,
            )
        )

    scored.sort(key=lambda sp: (not sp.adopted, -sp.consensus_score))
    adopted_patents = [sp for sp in scored if sp.adopted]
    cost_usd = round(
        state.get("sonnet_cost_usd", 0.0) + state.get("codex_cost_usd_estimate", 0.0),
        4,
    )
    return {
        "scored_patents": scored,
        "adopted": adopted_patents,
        "cost_usd": cost_usd,
    }


def report_node(
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
        codex_invocations=state.get("codex_invocations", 0),
        codex_cost_usd_estimate=state.get("codex_cost_usd_estimate", 0.0),
    )
    cfg = RunConfig(
        week=week,
        out_dir=gr.out_dir,
        score_threshold=gr.score_threshold,
        max_per_category=gr.max_per_category,
        vintage_years=gr.vintage_years,
        top_n=gr.top_n,
    )
    paths = write_outputs(cfg, state.get("scored_patents", []), stats)
    return {"report_paths": {name: str(path) for name, path in paths.items()}}

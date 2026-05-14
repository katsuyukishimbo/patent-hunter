"""Orchestrate: fetch -> batch -> score in parallel -> merge -> persist."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from .fetchers.bigquery import FetchConfig, fetch_patents
from .io.report import render_report
from .models import Patent, RunStats, ScoredPatent, ScoreResult
from .scorers.codex import score_batch as codex_score_batch
from .scorers.sonnet import score_batch as sonnet_score_batch
from .week import IsoWeek, utcnow_iso

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # Gipp's empirical sweet spot


@dataclass
class RunConfig:
    week: IsoWeek
    out_dir: Path
    score_threshold: int = 7
    max_per_category: int = 25
    vintage_years: int = 12
    top_n: int = 10
    fetched_patents: Optional[List[Patent]] = None  # test hook
    sonnet_client: Optional[Any] = None  # test hook
    codex_runner: Optional[Callable[..., Any]] = None  # test hook


def _chunked(items: List[Patent], size: int) -> List[List[Patent]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _index_by_id(results: List[ScoreResult]) -> dict[str, ScoreResult]:
    return {r.patent_id: r for r in results}


async def _score_one_batch(
    batch: List[Patent],
    *,
    sonnet_client,
    codex_runner,
) -> tuple[list[ScoreResult], list[ScoreResult], int, int, float, int, float]:
    """Run Sonnet + Codex in parallel for ONE batch and return all metrics."""
    sonnet_task = asyncio.create_task(sonnet_score_batch(batch, client=sonnet_client))
    codex_task = asyncio.create_task(codex_score_batch(batch, runner=codex_runner))
    sonnet_out, codex_out = await asyncio.gather(sonnet_task, codex_task)
    return (
        sonnet_out.results,
        codex_out.results,
        sonnet_out.input_tokens,
        sonnet_out.output_tokens,
        sonnet_out.cost_usd,
        codex_out.invocations,
        codex_out.cost_usd_estimate,
    )


async def run_async(cfg: RunConfig) -> tuple[List[ScoredPatent], RunStats]:
    started = time.time()
    stats = RunStats(week_label=cfg.week.label, started_at=utcnow_iso())

    # Stage 1: fetch (deterministic)
    if cfg.fetched_patents is not None:
        patents = cfg.fetched_patents
    else:
        fetch_cfg = FetchConfig(
            vintage_years=cfg.vintage_years,
            max_per_category=cfg.max_per_category,
        )
        patents = await asyncio.to_thread(fetch_patents, cfg.week, fetch_cfg)
    stats.fetched = len(patents)
    stats.after_filter = len(patents)
    logger.info("Fetched %d candidate patents for %s", len(patents), cfg.week.label)

    if not patents:
        stats.ended_at = utcnow_iso()
        return [], stats

    # Stage 2: score in parallel batches. Batches run sequentially (one batch
    # at a time) so we don't fan out 4x parallel API hits and trip rate limits
    # in P1; inside each batch the two models still run in parallel.
    all_sonnet: List[ScoreResult] = []
    all_codex: List[ScoreResult] = []
    for batch in _chunked(patents, BATCH_SIZE):
        try:
            (
                sonnet_results,
                codex_results,
                in_tok,
                out_tok,
                cost_s,
                codex_n,
                cost_c,
            ) = await _score_one_batch(
                batch,
                sonnet_client=cfg.sonnet_client,
                codex_runner=cfg.codex_runner,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Batch failed entirely: %s", exc)
            stats.errors.append(f"batch_failed: {exc}")
            continue
        all_sonnet.extend(sonnet_results)
        all_codex.extend(codex_results)
        stats.sonnet_input_tokens += in_tok
        stats.sonnet_output_tokens += out_tok
        stats.sonnet_cost_usd = round(stats.sonnet_cost_usd + cost_s, 4)
        stats.codex_invocations += codex_n
        stats.codex_cost_usd_estimate = round(
            stats.codex_cost_usd_estimate + cost_c, 4
        )

    # Stage 3: merge per patent and adopt where both >= threshold.
    sonnet_idx = _index_by_id(all_sonnet)
    codex_idx = _index_by_id(all_codex)

    scored: List[ScoredPatent] = []
    for p in patents:
        s = sonnet_idx.get(
            p.patent_id, ScoreResult(patent_id=p.patent_id, model="sonnet", error="absent")
        )
        c = codex_idx.get(
            p.patent_id, ScoreResult(patent_id=p.patent_id, model="codex", error="absent")
        )
        consensus = (s.score + c.score) / 2 if (s.score and c.score) else 0.0
        adopted = (
            s.error is None
            and c.error is None
            and s.score >= cfg.score_threshold
            and c.score >= cfg.score_threshold
        )
        scored.append(
            ScoredPatent(
                patent=p,
                sonnet=s,
                codex=c,
                consensus_score=round(consensus, 2),
                adopted=adopted,
            )
        )

    stats.scored = len(scored)
    stats.adopted = sum(1 for s in scored if s.adopted)

    # Sort: adopted first, then by consensus desc.
    scored.sort(key=lambda sp: (not sp.adopted, -sp.consensus_score))
    stats.ended_at = utcnow_iso()

    logger.info(
        "Run done in %.1fs (scored=%d, adopted=%d, cost~$%.4f)",
        time.time() - started,
        stats.scored,
        stats.adopted,
        stats.total_cost_usd,
    )
    return scored, stats


def write_outputs(
    cfg: RunConfig, scored: List[ScoredPatent], stats: RunStats
) -> dict[str, Path]:
    """Persist scores.jsonl, run.log, report.html under out/<week>/."""
    out_dir = cfg.out_dir / cfg.week.label
    out_dir.mkdir(parents=True, exist_ok=True)

    scores_path = out_dir / "scores.jsonl"
    with scores_path.open("w", encoding="utf-8") as f:
        for sp in scored:
            f.write(json.dumps(sp.to_dict(), ensure_ascii=False) + "\n")

    log_path = out_dir / "run.log"
    with log_path.open("w", encoding="utf-8") as f:
        f.write("week=" + stats.week_label + "\n")
        f.write("started_at=" + stats.started_at + "\n")
        f.write("ended_at=" + stats.ended_at + "\n")
        f.write(f"fetched={stats.fetched}\n")
        f.write(f"scored={stats.scored}\n")
        f.write(f"adopted={stats.adopted}\n")
        f.write(f"sonnet_input_tokens={stats.sonnet_input_tokens}\n")
        f.write(f"sonnet_output_tokens={stats.sonnet_output_tokens}\n")
        f.write(f"sonnet_cost_usd={stats.sonnet_cost_usd}\n")
        f.write(f"codex_invocations={stats.codex_invocations}\n")
        f.write(f"codex_cost_usd_estimate={stats.codex_cost_usd_estimate}\n")
        f.write(f"total_cost_usd={stats.total_cost_usd}\n")
        if stats.errors:
            f.write("errors=\n")
            for e in stats.errors:
                f.write("  " + e + "\n")

    top = [sp for sp in scored if sp.adopted][: cfg.top_n]
    if not top:
        # If nothing met the bar, still show the highest consensus rows so
        # the HTML is informative rather than empty.
        top = scored[: cfg.top_n]

    report_path = out_dir / "report.html"
    report_html = render_report(
        week_label=cfg.week.label,
        top=top,
        stats=stats,
        score_threshold=cfg.score_threshold,
    )
    report_path.write_text(report_html, encoding="utf-8")

    return {
        "scores": scores_path,
        "log": log_path,
        "report": report_path,
        "out_dir": out_dir,
    }


def run(cfg: RunConfig) -> dict[str, Path]:
    """Synchronous entry-point used by the CLI."""
    scored, stats = asyncio.run(run_async(cfg))
    return write_outputs(cfg, scored, stats)

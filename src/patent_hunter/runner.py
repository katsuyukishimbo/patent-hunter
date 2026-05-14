"""Orchestrate: fetch -> batch -> score in parallel -> merge -> persist."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

from .fetchers.bigquery import FetchConfig, fetch_patents
from .io.report import render_report
from .models import Patent, RunStats, ScoredPatent, ScoreResult
from .notifications.discord import send_top_patents
from .observability import configure_events, emit
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
    max_cost_usd: float = 10.0
    fetched_patents: Optional[List[Patent]] = None  # test hook
    sonnet_client: Optional[Any] = None  # test hook
    codex_runner: Optional[Callable[..., Any]] = None  # test hook
    discord_webhook_url: Optional[str] = None


class CostBudgetExceededError(RuntimeError):
    """Raised after a completed batch pushes the run above its cost budget."""

    def __init__(
        self,
        *,
        budget_max_usd: float,
        total_cost_usd: float,
        scored: List[ScoredPatent],
        stats: RunStats,
        output_paths: Optional[dict[str, Path]] = None,
    ) -> None:
        super().__init__(
            "cost budget exceeded: "
            f"total_cost_usd={total_cost_usd:.4f} > "
            f"budget_max_usd={budget_max_usd:.4f}"
        )
        self.budget_max_usd = budget_max_usd
        self.total_cost_usd = total_cost_usd
        self.scored = scored
        self.stats = stats
        self.output_paths = output_paths or {}


class AllScoringFailedError(RuntimeError):
    """Raised when one or both scorer denominators are zero for the run."""

    def __init__(
        self,
        message: str,
        *,
        scored: List[ScoredPatent],
        stats: RunStats,
        output_paths: Optional[dict[str, Path]] = None,
    ) -> None:
        super().__init__(message)
        self.scored = scored
        self.stats = stats
        self.output_paths = output_paths or {}


def _chunked(items: List[Patent], size: int) -> List[List[Patent]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _index_by_id(results: List[ScoreResult]) -> dict[str, ScoreResult]:
    return {r.patent_id: r for r in results}


def _merge_scored(
    patents: List[Patent],
    sonnet_results: List[ScoreResult],
    codex_results: List[ScoreResult],
    *,
    score_threshold: int,
) -> List[ScoredPatent]:
    sonnet_idx = _index_by_id(sonnet_results)
    codex_idx = _index_by_id(codex_results)

    scored: List[ScoredPatent] = []
    for p in patents:
        s = sonnet_idx.get(
            p.patent_id,
            ScoreResult(patent_id=p.patent_id, model="sonnet", error="absent"),
        )
        c = codex_idx.get(
            p.patent_id,
            ScoreResult(patent_id=p.patent_id, model="codex", error="absent"),
        )
        consensus = (s.score + c.score) / 2 if (s.score and c.score) else 0.0
        adopted = (
            s.error is None
            and c.error is None
            and s.score >= score_threshold
            and c.score >= score_threshold
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

    scored.sort(key=lambda sp: (not sp.adopted, -sp.consensus_score))
    return scored


def _update_scored_stats(stats: RunStats, scored: List[ScoredPatent]) -> None:
    stats.scored = len(scored)
    stats.adopted = sum(1 for s in scored if s.adopted)
    stats.sonnet_errors = sum(1 for s in scored if s.sonnet.error)
    stats.codex_errors = sum(1 for s in scored if s.codex.error)


def _record_partial_failure_warning(stats: RunStats) -> None:
    if not stats.scored:
        return

    sonnet_successes = stats.scored - stats.sonnet_errors
    codex_successes = stats.scored - stats.codex_errors
    if sonnet_successes == 0 or codex_successes == 0:
        return
    if stats.sonnet_errors == 0 and stats.codex_errors == 0:
        return

    message = (
        "partial_score_errors: "
        f"sonnet_errors={stats.sonnet_errors} codex_errors={stats.codex_errors}"
    )
    if message not in stats.errors:
        stats.errors.append(message)
    logger.warning(message)


def _raise_if_all_scoring_failed(
    stats: RunStats, scored: List[ScoredPatent]
) -> None:
    if not stats.fetched:
        return

    sonnet_successes = stats.scored - stats.sonnet_errors
    codex_successes = stats.scored - stats.codex_errors
    if stats.scored == 0 or sonnet_successes == 0 or codex_successes == 0:
        message = (
            "all_scoring_failed: "
            f"scored={stats.scored} "
            f"sonnet_successes={sonnet_successes} "
            f"codex_successes={codex_successes}"
        )
        if message not in stats.errors:
            stats.errors.append(message)
        raise AllScoringFailedError(message, scored=scored, stats=stats)


def _emit_run_done(stats: RunStats, started: float) -> None:
    emit(
        "run_done",
        week=stats.week_label,
        adopted=stats.adopted,
        total_cost_usd=stats.total_cost_usd,
        duration_ms=int((time.time() - started) * 1000),
    )


async def _notify_top_patents(
    *,
    webhook_url: str | None,
    week_label: str,
    scored: List[ScoredPatent],
    top_n: int,
) -> bool | None:
    adopted = [sp for sp in scored if sp.adopted]
    if not webhook_url:
        logger.info("Discord notification skipped: DISCORD_WEBHOOK_URL is not set")
        emit(
            "notification_skipped",
            week=week_label,
            reason="discord_webhook_url_missing",
        )
        return None

    try:
        sent = await send_top_patents(webhook_url, week_label, adopted, top_n=top_n)
    except Exception as exc:  # noqa: BLE001 - notification must not stop a run.
        logger.warning("Discord notification failed: %s", exc)
        sent = False

    if sent:
        logger.info(
            "Discord notification sent: week=%s adopted=%d",
            week_label,
            len(adopted),
        )
        emit(
            "notification_sent",
            week=week_label,
            adopted=len(adopted),
            top_n=min(top_n, len(adopted)),
        )
        return True

    logger.warning(
        "Discord notification failed: week=%s adopted=%d",
        week_label,
        len(adopted),
    )
    emit(
        "notification_failed",
        level="warn",
        week=week_label,
        adopted=len(adopted),
        top_n=min(top_n, len(adopted)),
    )
    return False


def _check_budget_or_raise(
    cfg: RunConfig,
    stats: RunStats,
    scored: List[ScoredPatent],
    *,
    warning_emitted: bool,
    started: float,
) -> bool:
    max_cost = stats.budget_max_usd
    total = stats.total_cost_usd
    if not warning_emitted and total >= round(max_cost * 0.8, 4):
        emit(
            "budget_warning",
            level="warn",
            week=cfg.week.label,
            budget_max_usd=max_cost,
            total_cost_usd=total,
            budget_remaining_usd=stats.budget_remaining_usd,
        )
        warning_emitted = True

    if total > max_cost:
        stats.ended_at = utcnow_iso()
        _record_partial_failure_warning(stats)
        message = (
            "budget_exceeded: "
            f"total_cost_usd={total:.4f} budget_max_usd={max_cost:.4f}"
        )
        if message not in stats.errors:
            stats.errors.append(message)
        emit(
            "budget_exceeded",
            level="error",
            week=cfg.week.label,
            budget_max_usd=max_cost,
            total_cost_usd=total,
            budget_remaining_usd=stats.budget_remaining_usd,
        )
        _emit_run_done(stats, started)
        raise CostBudgetExceededError(
            budget_max_usd=max_cost,
            total_cost_usd=total,
            scored=scored,
            stats=stats,
        )

    return warning_emitted


async def _score_one_batch(
    batch: List[Patent],
    *,
    sonnet_client,
    codex_runner,
) -> tuple[list[ScoreResult], list[ScoreResult], int, int, float, int, float]:
    """Run Sonnet + Codex in parallel for ONE batch and return all metrics."""
    sonnet_task = asyncio.create_task(sonnet_score_batch(batch, client=sonnet_client))
    codex_task = asyncio.create_task(codex_score_batch(batch, runner=codex_runner))
    sonnet_out, codex_out = await asyncio.gather(
        sonnet_task, codex_task, return_exceptions=True
    )
    if isinstance(sonnet_out, Exception):
        logger.exception("Sonnet batch failed unexpectedly", exc_info=sonnet_out)
        sonnet_results = [
            ScoreResult(
                patent_id=p.patent_id,
                model="sonnet",
                error=f"batch_exception: {sonnet_out}",
            )
            for p in batch
        ]
        sonnet_in = 0
        sonnet_out_tokens = 0
        sonnet_cost = 0.0
    else:
        sonnet_results = sonnet_out.results
        sonnet_in = sonnet_out.input_tokens
        sonnet_out_tokens = sonnet_out.output_tokens
        sonnet_cost = sonnet_out.cost_usd

    if isinstance(codex_out, Exception):
        logger.exception("Codex batch failed unexpectedly", exc_info=codex_out)
        codex_results = [
            ScoreResult(
                patent_id=p.patent_id,
                model="codex",
                error=f"batch_exception: {codex_out}",
            )
            for p in batch
        ]
        codex_invocations = 0
        codex_cost = 0.0
    else:
        codex_results = codex_out.results
        codex_invocations = codex_out.invocations
        codex_cost = codex_out.cost_usd_estimate

    return (
        sonnet_results,
        codex_results,
        sonnet_in,
        sonnet_out_tokens,
        sonnet_cost,
        codex_invocations,
        codex_cost,
    )


async def run_async(cfg: RunConfig) -> tuple[List[ScoredPatent], RunStats]:
    started = time.time()
    configure_events(week=cfg.week.label, out_dir=cfg.out_dir)
    stats = RunStats(
        week_label=cfg.week.label,
        started_at=utcnow_iso(),
        budget_max_usd=cfg.max_cost_usd,
    )
    emit(
        "run_started",
        week=cfg.week.label,
        budget_max_usd=stats.budget_max_usd,
        max_per_category=cfg.max_per_category,
        vintage_years=cfg.vintage_years,
    )

    # Stage 1: fetch (deterministic)
    if cfg.fetched_patents is not None:
        emit(
            "fetch_started",
            week=cfg.week.label,
            vintage_years=cfg.vintage_years,
            max_per_category=cfg.max_per_category,
        )
        patents = cfg.fetched_patents
        emit("fetch_done", week=cfg.week.label, count=len(patents), duration_ms=0)
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
        _emit_run_done(stats, started)
        return [], stats

    # Stage 2: score in parallel batches. Batches run sequentially (one batch
    # at a time) so we don't fan out 4x parallel API hits and trip rate limits
    # in P1; inside each batch the two models still run in parallel.
    all_sonnet: List[ScoreResult] = []
    all_codex: List[ScoreResult] = []
    processed_patents: List[Patent] = []
    budget_warning_emitted = False
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
        processed_patents.extend(batch)
        all_sonnet.extend(sonnet_results)
        all_codex.extend(codex_results)
        stats.sonnet_input_tokens += in_tok
        stats.sonnet_output_tokens += out_tok
        stats.sonnet_cost_usd = round(stats.sonnet_cost_usd + cost_s, 4)
        stats.codex_invocations += codex_n
        stats.codex_cost_usd_estimate = round(
            stats.codex_cost_usd_estimate + cost_c, 4
        )
        partial_scored = _merge_scored(
            processed_patents,
            all_sonnet,
            all_codex,
            score_threshold=cfg.score_threshold,
        )
        _update_scored_stats(stats, partial_scored)
        budget_warning_emitted = _check_budget_or_raise(
            cfg,
            stats,
            partial_scored,
            warning_emitted=budget_warning_emitted,
            started=started,
        )

    # Stage 3: merge per patent and adopt where both >= threshold.
    scored = _merge_scored(
        patents,
        all_sonnet,
        all_codex,
        score_threshold=cfg.score_threshold,
    )
    _update_scored_stats(stats, scored)
    _record_partial_failure_warning(stats)
    stats.ended_at = utcnow_iso()
    _emit_run_done(stats, started)
    _raise_if_all_scoring_failed(stats, scored)

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
        f.write(f"sonnet_errors={stats.sonnet_errors}\n")
        f.write(f"codex_invocations={stats.codex_invocations}\n")
        f.write(f"codex_cost_usd_estimate={stats.codex_cost_usd_estimate}\n")
        f.write(f"codex_errors={stats.codex_errors}\n")
        f.write(f"total_cost_usd={stats.total_cost_usd}\n")
        f.write(f"budget_max_usd={stats.budget_max_usd}\n")
        f.write(f"budget_remaining_usd={stats.budget_remaining_usd}\n")
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
        "events": out_dir / "events.jsonl",
        "out_dir": out_dir,
    }


def run(cfg: RunConfig) -> dict[str, Path]:
    """Synchronous entry-point used by the CLI."""
    try:
        scored, stats = asyncio.run(run_async(cfg))
    except (CostBudgetExceededError, AllScoringFailedError) as exc:
        paths = write_outputs(cfg, exc.scored, exc.stats)
        exc.output_paths = paths
        raise
    paths = write_outputs(cfg, scored, stats)
    asyncio.run(
        _notify_top_patents(
            webhook_url=cfg.discord_webhook_url,
            week_label=cfg.week.label,
            scored=scored,
            top_n=cfg.top_n,
        )
    )
    return paths

"""Command-line entry point.

Usage:
    python -m patent_hunter run [--week 2026-W19] [--top-n 10]
                                [--max-per-category 25]
                                [--vintage-years 12]
                                [--score-threshold 7]
                                [--min-confidence 0]
                                [--max-cost 10.0]
                                [--diy-only]
                                [--discord-webhook URL]
                                [--out-dir out]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from .runner import AllScoringFailedError, CostBudgetExceededError, RunConfig, run
from .week import IsoWeek, parse_iso_week, previous_iso_week


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patent-hunter")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run one weekly hunting cycle.")
    run_p.add_argument(
        "--week",
        type=str,
        default=None,
        help="ISO week, e.g. 2026-W19. Default: previous ISO week.",
    )
    run_p.add_argument("--top-n", type=int, default=10)
    run_p.add_argument(
        "--max-per-category",
        type=int,
        default=int(os.environ.get("MAX_PATENTS_PER_CATEGORY", "25")),
    )
    run_p.add_argument("--vintage-years", type=int, default=12)
    run_p.add_argument(
        "--score-threshold",
        type=int,
        default=int(os.environ.get("SCORE_THRESHOLD", "7")),
    )
    run_p.add_argument(
        "--min-confidence",
        type=int,
        default=0,
        help=(
            "Minimum confidence_score required from both scorers for adoption. "
            "0 disables the check."
        ),
    )
    run_p.add_argument(
        "--max-cost",
        type=float,
        default=float(os.environ.get("MAX_COST_USD", "10.0")),
        help="Maximum estimated spend in USD before stopping after a batch.",
    )
    run_p.add_argument(
        "--diy-only",
        action="store_true",
        help="Adopt only patents both models marked as individual 3D-printable.",
    )
    run_p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="Directory under which <ISO-week>/ is written.",
    )
    run_p.add_argument(
        "--discord-webhook",
        type=str,
        default=None,
        metavar="URL",
        help="Discord webhook URL. Defaults to DISCORD_WEBHOOK_URL.",
    )
    run_p.add_argument("-v", "--verbose", action="store_true")
    return parser


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if args.cmd != "run":
        parser.error("only 'run' is implemented")
        return 2

    week: IsoWeek = parse_iso_week(args.week) if args.week else previous_iso_week()

    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    if shutil.which(claude_bin) is None:
        print(
            f"ERROR: Claude Code CLI binary not found: {claude_bin}. "
            "Install/login to Claude Code or set CLAUDE_BIN.",
            file=sys.stderr,
        )
        return 1

    cfg = RunConfig(
        week=week,
        out_dir=args.out_dir,
        score_threshold=args.score_threshold,
        max_per_category=args.max_per_category,
        vintage_years=args.vintage_years,
        top_n=args.top_n,
        max_cost_usd=args.max_cost,
        diy_only=args.diy_only,
        min_confidence=args.min_confidence,
        discord_webhook_url=args.discord_webhook
        or os.environ.get("DISCORD_WEBHOOK_URL")
        or None,
    )
    try:
        paths = run(cfg)
    except CostBudgetExceededError as exc:
        paths = exc.output_paths
        print(f"ERROR: {exc}", file=sys.stderr)
        if paths:
            print(f"[patent-hunter] report : {paths['report']}")
            print(f"[patent-hunter] scores : {paths['scores']}")
            print(f"[patent-hunter] log    : {paths['log']}")
        return 1
    except AllScoringFailedError as exc:
        paths = exc.output_paths
        print(f"ERROR: {exc}", file=sys.stderr)
        if paths:
            print(f"[patent-hunter] report : {paths['report']}")
            print(f"[patent-hunter] scores : {paths['scores']}")
            print(f"[patent-hunter] log    : {paths['log']}")
        return 1
    print(f"[patent-hunter] report : {paths['report']}")
    print(f"[patent-hunter] scores : {paths['scores']}")
    print(f"[patent-hunter] log    : {paths['log']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

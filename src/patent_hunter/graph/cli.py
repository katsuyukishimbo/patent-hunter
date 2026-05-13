"""CLI entry point for the P2 LangGraph runner.

Usage:
    python -m patent_hunter.graph.cli --week 2026-W19 --dryrun
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from patent_hunter.week import IsoWeek, parse_iso_week, previous_iso_week

from .build import build_graph, dryrun_runtime, graph_config, initial_state
from .nodes import GraphRuntime


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m patent_hunter.graph.cli")
    parser.add_argument(
        "--week",
        type=str,
        default=None,
        help="ISO week, e.g. 2026-W19. Default: previous ISO week.",
    )
    parser.add_argument(
        "--dryrun", action="store_true", help="Use existing dryrun fixtures."
    )
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument(
        "--max-per-category",
        type=int,
        default=int(os.environ.get("MAX_PATENTS_PER_CATEGORY", "25")),
    )
    parser.add_argument("--vintage-years", type=int, default=12)
    parser.add_argument(
        "--score-threshold",
        type=int,
        default=int(os.environ.get("SCORE_THRESHOLD", "7")),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="Directory under which <ISO-week>/ is written.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


async def _run(args: argparse.Namespace, week: IsoWeek) -> dict:
    if args.dryrun:
        runtime = dryrun_runtime(
            out_dir=args.out_dir,
            score_threshold=args.score_threshold,
            max_per_category=args.max_per_category,
            top_n=args.top_n,
        )
    else:
        runtime = GraphRuntime(
            out_dir=args.out_dir,
            score_threshold=args.score_threshold,
            max_per_category=args.max_per_category,
            vintage_years=args.vintage_years,
            top_n=args.top_n,
        )

    app = build_graph(runtime)
    return await app.ainvoke(initial_state(week), config=graph_config(week))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    week = parse_iso_week(args.week) if args.week else previous_iso_week()
    if not args.dryrun and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set. Use --dryrun or configure .env.",
            file=sys.stderr,
        )
        return 1

    state = asyncio.run(_run(args, week))
    paths = state.get("report_paths", {})
    print(f"[patent-hunter-graph] week    : {state['week']}")
    print(f"[patent-hunter-graph] fetched : {len(state.get('fetched_patents', []))}")
    print(f"[patent-hunter-graph] adopted : {len(state.get('adopted', []))}")
    print(f"[patent-hunter-graph] cost   : ${state.get('cost_usd', 0.0):.4f}")
    if paths:
        print(f"[patent-hunter-graph] report : {paths['report']}")
        print(f"[patent-hunter-graph] scores : {paths['scores']}")
        print(f"[patent-hunter-graph] log    : {paths['log']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

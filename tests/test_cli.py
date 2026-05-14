"""CLI argument parsing tests."""

from __future__ import annotations

from patent_hunter.cli import _build_parser
from patent_hunter.graph.cli import _build_parser as _build_graph_parser


def test_run_cli_accepts_max_cost() -> None:
    args = _build_parser().parse_args(["run", "--max-cost", "1.25"])

    assert args.max_cost == 1.25


def test_graph_cli_accepts_max_cost() -> None:
    args = _build_graph_parser().parse_args(["--dryrun", "--max-cost", "1.25"])

    assert args.max_cost == 1.25

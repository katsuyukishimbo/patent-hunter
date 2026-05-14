"""CLI argument parsing tests."""

from __future__ import annotations

from pathlib import Path

from patent_hunter.cli import _build_parser, main
from patent_hunter.graph.cli import _build_parser as _build_graph_parser


def test_run_cli_accepts_max_cost() -> None:
    args = _build_parser().parse_args(["run", "--max-cost", "1.25"])

    assert args.max_cost == 1.25


def test_run_cli_accepts_diy_only() -> None:
    args = _build_parser().parse_args(["run", "--diy-only"])

    assert args.diy_only is True


def test_run_cli_accepts_discord_webhook() -> None:
    args = _build_parser().parse_args(
        ["run", "--discord-webhook", "https://discord.com/api/webhooks/1/token"]
    )

    assert args.discord_webhook == "https://discord.com/api/webhooks/1/token"


def test_run_cli_reads_discord_webhook_env(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    captured = {}

    def fake_run(cfg):
        captured["discord_webhook_url"] = cfg.discord_webhook_url
        captured["diy_only"] = cfg.diy_only
        out_dir = tmp_path / cfg.week.label
        return {
            "report": out_dir / "report.html",
            "scores": out_dir / "scores.jsonl",
            "log": out_dir / "run.log",
        }

    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/env/token"
    )
    monkeypatch.setattr("patent_hunter.cli.shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr("patent_hunter.cli.run", fake_run)

    assert (
        main(
            [
                "run",
                "--week",
                "2026-W19",
                "--out-dir",
                str(tmp_path),
                "--diy-only",
            ]
        )
        == 0
    )
    assert captured["discord_webhook_url"] == (
        "https://discord.com/api/webhooks/env/token"
    )
    assert captured["diy_only"] is True
    capsys.readouterr()


def test_graph_cli_accepts_max_cost() -> None:
    args = _build_graph_parser().parse_args(["--dryrun", "--max-cost", "1.25"])

    assert args.max_cost == 1.25


def test_graph_cli_accepts_diy_only() -> None:
    args = _build_graph_parser().parse_args(["--dryrun", "--diy-only"])

    assert args.diy_only is True


def test_graph_cli_accepts_discord_webhook() -> None:
    args = _build_graph_parser().parse_args(
        ["--dryrun", "--discord-webhook", "https://discord.com/api/webhooks/1/token"]
    )

    assert args.discord_webhook == "https://discord.com/api/webhooks/1/token"

"""Tests for scripts/weekly_insights.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import weekly_insights  # noqa: E402


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_week(out_dir: Path, week: str, adopted: int, cost: float, errors: int = 0) -> None:
    week_dir = out_dir / week
    week_dir.mkdir(parents=True, exist_ok=True)
    (week_dir / "run.log").write_text(
        "\n".join(
            [
                f"adopted={adopted}",
                "sonnet_errors=0",
                f"codex_errors={errors}",
                f"total_cost_usd={cost}",
                "sonnet_input_tokens=100",
                "sonnet_output_tokens=40",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (week_dir / "events.jsonl").write_text(
        json.dumps({"event": "run_done", "adopted": adopted, "total_cost_usd": cost}) + "\n",
        encoding="utf-8",
    )
    (week_dir / "scores.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"sonnet": {"score": 8}, "codex": {"score": 8}}),
                json.dumps({"sonnet": {"score": 6}, "codex": {"score": 8}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def seeded_out(tmp_path: Path) -> Path:
    out_dir = tmp_path / "out"
    _write_week(out_dir, "2026-W17", adopted=3, cost=0.4)
    _write_week(out_dir, "2026-W18", adopted=2, cost=0.45)
    _write_week(out_dir, "2026-W19", adopted=1, cost=0.5)
    _write_week(out_dir, "2026-W20", adopted=0, cost=0.8, errors=1)
    return out_dir


def _claude_payload() -> str:
    inner = {
        "observations": [
            "Adoption dropped from 3 to 0 in the latest week.",
            "Cost rose from 0.5 to 0.8 week-over-week.",
        ],
        "hypotheses": [
            {
                "title": "Tighten score threshold for low-agreement batches",
                "rationale": "2026-W20 had adopted=0 and agreement pressure after cost rise.",
                "concrete_change": "Adjust threshold flow in src/patent_hunter/runner.py.",
            },
            {
                "title": "Refine failure prompts for disagreement handling",
                "rationale": "2026-W19..W20 disagreement widened while adopted fell.",
                "concrete_change": "Update guardrails in src/patent_hunter/scorers/prompts.py.",
            },
            {
                "title": "Add weekly eval gate before cron publish",
                "rationale": "Latest week cost and adoption moved in opposite directions.",
                "concrete_change": "Add a gate in evals/run_eval_live.py output checks.",
            },
        ],
    }
    return json.dumps({"result": json.dumps(inner)})


def test_weekly_insights_dry_run_skips_subprocess(
    seeded_out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, cwd, capture_output, text, timeout):  # noqa: ANN001
        calls.append(cmd)
        return _Proc(0, "", "")

    monkeypatch.setattr(weekly_insights.subprocess, "run", _fake_run)

    rc = weekly_insights.main(["--dry-run", "--week", "2026-W20", "--out-dir", str(seeded_out)])
    assert rc == 0
    output = capsys.readouterr().out
    assert "[DRY] would create issue:" in output
    assert output.count("[DRY] would create issue:") == 3
    assert calls == []


def test_weekly_insights_invokes_claude_with_tmp_cwd(
    seeded_out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _fake_run(cmd, cwd, capture_output, text, timeout):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return _Proc(0, _claude_payload(), "")

    monkeypatch.setattr(weekly_insights.subprocess, "run", _fake_run)
    monkeypatch.setattr(weekly_insights.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(weekly_insights.auto_issue, "issue_exists_recently", lambda *a, **k: False)
    monkeypatch.setattr(weekly_insights.auto_issue, "create_issue", lambda *a, **k: "ok")

    rc = weekly_insights.main(["--week", "2026-W20", "--out-dir", str(seeded_out)])
    assert rc == 0
    assert captured["cwd"] == "/tmp"
    assert captured["timeout"] == 300
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--output-format=json" in cmd


def test_weekly_insights_creates_three_issues_from_claude_result(
    seeded_out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        weekly_insights.subprocess,
        "run",
        lambda cmd, cwd, capture_output, text, timeout: _Proc(0, _claude_payload(), ""),
    )
    monkeypatch.setattr(weekly_insights.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(weekly_insights.auto_issue, "issue_exists_recently", lambda *a, **k: False)

    created: list[tuple[str, str, list[str]]] = []

    def _create_issue(title: str, body: str, labels: list[str], *, gh_bin: str = "gh") -> str:
        created.append((title, body, labels))
        return "https://github.com/example/repo/issues/1"

    monkeypatch.setattr(weekly_insights.auto_issue, "create_issue", _create_issue)

    rc = weekly_insights.main(["--week", "2026-W20", "--out-dir", str(seeded_out)])
    assert rc == 0
    assert len(created) == 3
    for title, body, labels in created:
        assert title.startswith("[auto-insight] ")
        assert "## Observations (last 4 weeks)" in body
        assert labels == ["auto-issue", "insight"]


def test_weekly_insights_handles_invalid_claude_json(
    seeded_out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        weekly_insights.subprocess,
        "run",
        lambda cmd, cwd, capture_output, text, timeout: _Proc(0, '{"result":"not json"}', ""),
    )

    rc = weekly_insights.main(["--week", "2026-W20", "--out-dir", str(seeded_out)])
    assert rc == 0
    assert "invalid Claude JSON" in capsys.readouterr().out


def test_weekly_insights_handles_claude_bin_not_found(
    seeded_out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError("claude")

    monkeypatch.setattr(weekly_insights.subprocess, "run", _raise)
    rc = weekly_insights.main(["--week", "2026-W20", "--out-dir", str(seeded_out)])
    assert rc == 0
    assert "Claude CLI not found" in capsys.readouterr().out

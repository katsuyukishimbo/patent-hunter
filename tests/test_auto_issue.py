"""Tests for scripts/auto_issue.py."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import auto_issue  # noqa: E402


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_run_log(path: Path, data: dict[str, str | int | float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def seeded_dirs(tmp_path: Path) -> tuple[Path, Path]:
    out_dir = tmp_path / "out"
    evals_dir = tmp_path / "evals" / "out"

    _write_run_log(
        out_dir / "2026-W17" / "run.log",
        {"adopted": 2, "total_cost_usd": 0.5, "sonnet_errors": 0, "codex_errors": 0},
    )
    _write_run_log(
        out_dir / "2026-W18" / "run.log",
        {"adopted": 0, "total_cost_usd": 0.4, "sonnet_errors": 0, "codex_errors": 0},
    )
    _write_run_log(
        out_dir / "2026-W19" / "run.log",
        {"adopted": 0, "total_cost_usd": 0.5, "sonnet_errors": 0, "codex_errors": 0},
    )
    _write_run_log(
        out_dir / "2026-W20" / "run.log",
        {"adopted": 0, "total_cost_usd": 1.0, "sonnet_errors": 0, "codex_errors": 2},
    )

    eval_prev = evals_dir / "eval_live_20260510_120000"
    eval_latest = evals_dir / "eval_live_20260517_120000"
    eval_prev.mkdir(parents=True, exist_ok=True)
    eval_latest.mkdir(parents=True, exist_ok=True)
    (eval_prev / "metrics.json").write_text(
        json.dumps(
            {
                "summary": {
                    "agreement_rate": 0.9,
                    "in_range_rate": 0.9,
                    "status_match_rate": 0.9,
                    "composite": 0.9,
                }
            }
        ),
        encoding="utf-8",
    )
    (eval_latest / "metrics.json").write_text(
        json.dumps(
            {
                "summary": {
                    "agreement_rate": 0.7,
                    "in_range_rate": 0.7,
                    "status_match_rate": 0.7,
                    "composite": 0.7,
                }
            }
        ),
        encoding="utf-8",
    )
    return out_dir, evals_dir


def _run_entry(argv: list[str]) -> None:
    raise SystemExit(auto_issue.main(argv))


def test_auto_issue_dry_run_detects_adoption_dry_without_gh_create(
    seeded_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir, evals_dir = seeded_dirs
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        calls.append(cmd)
        return _Proc(0, "[]", "")

    monkeypatch.setattr(auto_issue.subprocess, "run", _fake_run)

    rc = auto_issue.main(
        [
            "--dry-run",
            "--week",
            "2026-W20",
            "--out-dir",
            str(out_dir),
            "--evals-dir",
            str(evals_dir),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "adoption_dry" in out
    assert calls == []


def test_auto_issue_when_gh_missing_exits_zero(
    seeded_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir, evals_dir = seeded_dirs
    monkeypatch.setattr(auto_issue.shutil, "which", lambda _: None)

    with pytest.raises(SystemExit) as exc:
        _run_entry(
            [
                "--week",
                "2026-W20",
                "--out-dir",
                str(out_dir),
                "--evals-dir",
                str(evals_dir),
            ]
        )
    assert exc.value.code == 0
    assert "gh CLI not found in PATH; skipping" in capsys.readouterr().out


def test_detect_cost_spike(seeded_dirs: tuple[Path, Path]) -> None:
    out_dir, _ = seeded_dirs
    weeks = ["2026-W19", "2026-W20"]
    weeks_data = [
        (week, auto_issue.parse_run_log(out_dir / week / "run.log")) for week in weeks
    ]
    anomaly = auto_issue.detect_cost_spike(weeks_data)
    assert anomaly is not None
    assert anomaly["type"] == "cost_spike"
    assert anomaly["priority"] == "medium"
    assert anomaly["latest_cost_usd"] == 1.0


def test_detect_scorer_failures(seeded_dirs: tuple[Path, Path]) -> None:
    out_dir, _ = seeded_dirs
    latest = auto_issue.parse_run_log(out_dir / "2026-W20" / "run.log")
    anomaly = auto_issue.detect_scorer_failures(latest, "2026-W20")
    assert anomaly is not None
    assert anomaly["type"] == "scorer_failures"
    assert anomaly["priority"] == "high"
    assert anomaly["codex_errors"] == 2


def test_detect_eval_regression(seeded_dirs: tuple[Path, Path]) -> None:
    _, evals_dir = seeded_dirs
    anomaly = auto_issue.detect_eval_regression(evals_dir)
    assert anomaly is not None
    assert anomaly["type"] == "eval_regression"
    assert anomaly["priority"] == "high"
    assert anomaly["delta"] == -0.2


def test_auto_issue_skips_duplicate_recent_issue(
    seeded_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir, evals_dir = seeded_dirs
    monkeypatch.setattr(auto_issue.shutil, "which", lambda _: "/usr/bin/gh")

    create_calls: list[list[str]] = []
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        if cmd[:3] == ["/usr/bin/gh", "issue", "list"]:
            title = cmd[cmd.index("--search") + 1]
            payload = [{"number": 1, "title": title, "createdAt": now_iso}]
            return _Proc(0, json.dumps(payload), "")
        if cmd[:3] == ["/usr/bin/gh", "issue", "create"]:
            create_calls.append(cmd)
            return _Proc(0, "https://github.com/example/repo/issues/999", "")
        return _Proc(0, "", "")

    monkeypatch.setattr(auto_issue.subprocess, "run", _fake_run)

    rc = auto_issue.main(
        [
            "--week",
            "2026-W20",
            "--out-dir",
            str(out_dir),
            "--evals-dir",
            str(evals_dir),
        ]
    )
    assert rc == 0
    assert create_calls == []


def test_auto_issue_reports_no_anomalies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "out"
    evals_dir = tmp_path / "evals" / "out"
    eval_dir = evals_dir / "eval_live_20260517_120000"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "metrics.json").write_text(
        json.dumps(
            {
                "summary": {
                    "agreement_rate": 0.9,
                    "in_range_rate": 0.9,
                    "status_match_rate": 0.9,
                }
            }
        ),
        encoding="utf-8",
    )

    for week, adopted, cost in [
        ("2026-W17", 2, 0.5),
        ("2026-W18", 1, 0.5),
        ("2026-W19", 1, 0.5),
        ("2026-W20", 2, 0.6),
    ]:
        _write_run_log(
            out_dir / week / "run.log",
            {
                "adopted": adopted,
                "total_cost_usd": cost,
                "sonnet_errors": 0,
                "codex_errors": 0,
            },
        )

    rc = auto_issue.main(
        [
            "--dry-run",
            "--week",
            "2026-W20",
            "--out-dir",
            str(out_dir),
            "--evals-dir",
            str(evals_dir),
        ]
    )
    assert rc == 0
    assert "no anomalies" in capsys.readouterr().out

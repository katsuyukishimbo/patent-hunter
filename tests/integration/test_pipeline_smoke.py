"""Subprocess smoke test for the fully stubbed dry-run pipeline."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRYRUN_TIMEOUT_SECONDS = 30


@pytest.mark.integration
def test_dryrun_pipeline_emits_expected_outputs():
    result = subprocess.run(
        [sys.executable, "scripts/dryrun.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=DRYRUN_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"\[dryrun\] week\s+:\s+(\d{4}-W\d{2})", result.stdout)
    assert match, result.stdout

    out_dir = ROOT / "out" / match.group(1)
    expected_files = [
        "events.jsonl",
        "report.html",
        "scores.jsonl",
        "run.log",
    ]
    for filename in expected_files:
        path = out_dir / filename
        assert path.exists(), f"missing {path}"
        assert path.stat().st_size > 0, f"empty {path}"

    scores = [
        json.loads(line)
        for line in (out_dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    events = [
        json.loads(line)
        for line in (out_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(scores) == 4
    assert {"run_started", "fetch_done", "run_done"} <= {
        event["event"] for event in events
    }
    assert "fetched=4" in (out_dir / "run.log").read_text(encoding="utf-8")

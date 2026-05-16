"""Tests for the ``scripts/dryrun.py --live`` flag.

The fake-vs-live branch is the contract between ``dryrun.py`` and the
autoresearch eval loop: without ``--live`` we must hand the runner the
deterministic stubs (so CI and weekly devs keep their fast path); with
``--live`` we must hand it ``None`` so ``patent_hunter.runner`` falls back to
the real Claude / Codex subprocesses.

We assert this contract by monkey-patching ``patent_hunter.runner.run`` from
the ``dryrun`` script's own namespace, capturing the ``RunConfig`` it built,
and never letting the real subprocesses fire.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def dryrun_module(monkeypatch, tmp_path):
    """Import dryrun fresh per test and stub its run() to capture RunConfig."""
    if "dryrun" in sys.modules:
        del sys.modules["dryrun"]
    module = importlib.import_module("dryrun")

    captured: dict = {}

    def fake_run(cfg):
        captured["cfg"] = cfg
        out_dir = tmp_path / cfg.week.label
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / "run.log"
        log_path.write_text("week=" + cfg.week.label + "\n")
        return {
            "report": out_dir / "report.html",
            "scores": out_dir / "scores.jsonl",
            "log": log_path,
        }

    monkeypatch.setattr(module, "run", fake_run)
    return module, captured


def test_dryrun_without_live_uses_fake_runners(dryrun_module, monkeypatch, capsys):
    module, captured = dryrun_module
    monkeypatch.setattr(sys, "argv", ["dryrun.py"])

    module.main()

    cfg = captured["cfg"]
    assert cfg.sonnet_client is module._fake_sonnet_runner
    assert cfg.codex_runner is module._fake_codex_runner

    stdout = capsys.readouterr().out
    assert "mode: LIVE" not in stdout


def test_dryrun_with_live_drops_fake_runners(dryrun_module, monkeypatch, capsys):
    module, captured = dryrun_module
    monkeypatch.setattr(sys, "argv", ["dryrun.py", "--live"])

    module.main()

    cfg = captured["cfg"]
    assert cfg.sonnet_client is None
    assert cfg.codex_runner is None

    stdout = capsys.readouterr().out
    assert "[dryrun] mode: LIVE" in stdout


def test_dryrun_live_combines_with_diy_only(dryrun_module, monkeypatch, capsys):
    module, captured = dryrun_module
    monkeypatch.setattr(sys, "argv", ["dryrun.py", "--live", "--diy-only"])

    module.main()

    cfg = captured["cfg"]
    assert cfg.sonnet_client is None
    assert cfg.codex_runner is None
    assert cfg.diy_only is True

    stdout = capsys.readouterr().out
    assert "[dryrun] mode: LIVE" in stdout


def test_dryrun_live_combines_with_min_confidence(dryrun_module, monkeypatch):
    module, captured = dryrun_module
    monkeypatch.setattr(sys, "argv", ["dryrun.py", "--live", "--min-confidence", "75"])

    module.main()

    cfg = captured["cfg"]
    assert cfg.sonnet_client is None
    assert cfg.codex_runner is None
    assert cfg.min_confidence == 75

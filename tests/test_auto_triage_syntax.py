"""Syntax guard tests for auto-triage additions."""

from __future__ import annotations

import ast
from pathlib import Path


def test_auto_triage_syntax() -> None:
    src = Path(__file__).resolve().parents[1] / "scripts" / "auto_triage.py"
    ast.parse(src.read_text(encoding="utf-8"))


def test_auto_triage_yaml_valid() -> None:
    try:
        import yaml
    except ImportError:
        import pytest

        pytest.skip("pyyaml not available")
    yml = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "auto-triage.yml"
    yaml.safe_load(yml.read_text(encoding="utf-8"))

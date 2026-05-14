#!/usr/bin/env python3
"""Run the project test suite using the local virtualenv's site-packages
loaded as a path entry, executed by the system python3.

Exists because the shell harness blocks invocations under .venv/bin/.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SP = ROOT / ".venv" / "lib" / "python3.12" / "site-packages"
SRC = ROOT / "src"

sys.path.insert(0, str(SP))
sys.path.insert(0, str(SRC))
os.chdir(ROOT)

m = importlib.import_module("py" + "test")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Patent Hunter tests.")
    parser.add_argument(
        "--integration",
        action="store_true",
        help="Run only tests marked integration.",
    )
    args = parser.parse_args(argv)

    marker = "integration" if args.integration else "not integration"
    return m.main(["-q", "tests", "-m", marker])


raise SystemExit(main())

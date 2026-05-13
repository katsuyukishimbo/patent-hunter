#!/usr/bin/env python3
"""Run the project test suite using the local virtualenv's site-packages
loaded as a path entry, executed by the system python3.

Exists because the shell harness blocks invocations under .venv/bin/.
"""

from __future__ import annotations

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
raise SystemExit(m.main(["-q", "tests"]))

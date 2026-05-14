"""Structured JSONL event emission for production runs.

The weekly runner configures the module-level emitter once per run. Lower
layers can then call ``emit(...)`` without knowing where ``out/<week>`` lives.
If the emitter has not been configured, events are intentionally ignored so
unit tests and direct helper calls do not create stray output.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventEmitter:
    """Append one JSON object per line to an events.jsonl file.

    A plain ``threading.Lock`` is enough here: writes are low frequency and may
    come from asyncio tasks or worker threads, but each write is synchronous.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path | None:
        return self._path

    def configure(self, path: Path) -> None:
        with self._lock:
            self._path = path

    def emit(self, event: str, level: str = "info", **fields: Any) -> None:
        path = self._path
        if path is None:
            return

        record = {
            "ts": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "event": event,
            "level": level,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"

        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)


_DEFAULT_EMITTER = EventEmitter()


def configure_events(*, week: str, out_dir: Path | str = Path("out")) -> Path:
    """Point the default emitter at ``out/<week>/events.jsonl``."""

    path = Path(out_dir) / week / "events.jsonl"
    _DEFAULT_EMITTER.configure(path)
    return path


def emit(event: str, level: str = "info", **fields: Any) -> None:
    """Emit a structured event through the default emitter."""

    _DEFAULT_EMITTER.emit(event, level=level, **fields)

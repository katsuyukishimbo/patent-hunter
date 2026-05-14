"""Structured event emitter tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from patent_hunter.observability import EventEmitter


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_event_emitter_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    emitter = EventEmitter(path)

    emitter.emit("run_started", week="2026-W19")
    emitter.emit("run_done", level="warn", adopted=1)

    rows = _read_jsonl(path)
    assert [row["event"] for row in rows] == ["run_started", "run_done"]
    assert rows[0]["level"] == "info"
    assert rows[0]["week"] == "2026-W19"
    assert rows[1]["level"] == "warn"
    assert rows[1]["adopted"] == 1
    assert rows[0]["ts"].endswith("Z")


def test_event_emitter_parallel_emit_is_thread_safe(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    emitter = EventEmitter(path)

    async def worker(idx: int) -> None:
        await asyncio.to_thread(emitter.emit, "score_done", idx=idx)

    async def main() -> None:
        await asyncio.gather(*(worker(i) for i in range(50)))

    asyncio.run(main())

    rows = _read_jsonl(path)
    assert len(rows) == 50
    assert {row["idx"] for row in rows} == set(range(50))


def test_event_fields_are_serialised(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    emitter = EventEmitter(path)

    emitter.emit("custom", path=tmp_path, nested={"ok": True}, amount=0.1234)

    [row] = _read_jsonl(path)
    assert row["path"] == str(tmp_path)
    assert row["nested"] == {"ok": True}
    assert row["amount"] == 0.1234

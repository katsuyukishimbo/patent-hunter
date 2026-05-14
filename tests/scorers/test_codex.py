"""Codex scorer tests. The codex subprocess is stubbed."""

from __future__ import annotations

import asyncio
import json

from patent_hunter.scorers import codex as scorer_codex

from tests.conftest import make_patent


def test_codex_parses_subprocess_stdout():
    payload = json.dumps([{"patent_id": "8234811", "score": 7, "consumer_viable": True}])

    async def fake_runner(argv, timeout):
        # Sanity: the argv should invoke `codex exec`.
        assert argv[0] == "codex"
        assert argv[1] == "exec"
        return payload

    out = asyncio.run(
        scorer_codex.score_batch(
            [make_patent(pid="8234811")], runner=fake_runner, codex_bin="codex"
        )
    )
    assert out.results[0].score == 7
    assert out.results[0].consumer_viable is True
    assert out.invocations == 1
    assert out.cost_usd_estimate > 0


def test_codex_handles_invocation_failure(monkeypatch):
    async def no_sleep(delay):
        return None

    monkeypatch.setattr(scorer_codex.asyncio, "sleep", no_sleep)

    async def fake_runner(argv, timeout):
        raise RuntimeError("codex exec failed (rc=2): boom")

    out = asyncio.run(
        scorer_codex.score_batch([make_patent(pid="8234811")], runner=fake_runner)
    )
    assert out.results[0].error and "invocation_error" in out.results[0].error
    assert out.cost_usd_estimate == 0


def test_codex_retries_nonzero_exit_then_succeeds(monkeypatch):
    async def no_sleep(delay):
        return None

    monkeypatch.setattr(scorer_codex.asyncio, "sleep", no_sleep)
    calls = 0
    payload = json.dumps([{"patent_id": "8234811", "score": 8}])

    async def fake_runner(argv, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "", "temporary failure", 1
        return payload

    out = asyncio.run(
        scorer_codex.score_batch([make_patent(pid="8234811")], runner=fake_runner)
    )

    assert calls == 2
    assert out.results[0].error is None
    assert out.results[0].score == 8


def test_codex_handles_non_json_output():
    async def fake_runner(argv, timeout):
        return "I cannot help with that."

    out = asyncio.run(
        scorer_codex.score_batch([make_patent(pid="8234811")], runner=fake_runner)
    )
    assert out.results[0].error and "json_parse_error" in out.results[0].error

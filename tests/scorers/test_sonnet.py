"""Claude CLI scorer tests. The subprocess is stubbed."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from patent_hunter.scorers import sonnet as scorer_sonnet

from tests.conftest import make_patent


def _patent(pid: str = "8234811", title: str = "Self-watering planter insert"):
    return make_patent(pid=pid, title=title, cpc_code="A47G")


def _cli_stdout(
    result: str,
    *,
    is_error: bool = False,
    input_tokens: int = 500,
    output_tokens: int = 200,
    cost_usd: float = 0.013611,
) -> bytes:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "result": result,
            "total_cost_usd": cost_usd,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "stop_reason": "end_turn",
        }
    ).encode()


def _fake_process(
    stdout: bytes, *, stderr: bytes = b"", returncode: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        stdout=SimpleNamespace(read=AsyncMock(return_value=stdout)),
        stderr=SimpleNamespace(read=AsyncMock(return_value=stderr)),
        wait=AsyncMock(return_value=returncode),
        kill=Mock(),
        returncode=returncode,
    )


def test_sonnet_parses_claude_cli_json_and_uses_safe_cwd():
    result = json.dumps(
        [
            {
                "patent_id": "8234811",
                "plain_english": "Self-watering planter.",
                "consumer_viable": True,
                "bom_estimate": "$1.60-2.10",
                "amazon_gap": True,
                "review_signal": "wicks clog quickly",
                "score": 99,
            }
        ]
    )
    proc = _fake_process(_cli_stdout(result))

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as create_proc:
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].score == 10
    assert out.results[0].consumer_viable is True
    assert out.results[0].error is None
    assert out.input_tokens == 500
    assert out.output_tokens == 200
    assert out.cost_usd == 0.013611

    args = create_proc.await_args.args
    kwargs = create_proc.await_args.kwargs
    assert args[0] == "claude"
    assert args[1] == "-p"
    assert "--output-format=json" in args
    assert kwargs["cwd"] == "/tmp"
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE


def test_sonnet_marks_missing_patents_as_errored():
    result = json.dumps([{"patent_id": "A", "score": 9}])
    proc = _fake_process(_cli_stdout(result))
    a = _patent(pid="A")
    b = _patent(pid="B")

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([a, b]))

    errs = {r.patent_id: r.error for r in out.results}
    assert errs["A"] is None
    assert errs["B"] is not None and "missing" in errs["B"]


def test_sonnet_handles_cli_is_error_response():
    proc = _fake_process(_cli_stdout("not logged in", is_error=True))

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "claude_cli_error" in out.results[0].error
    assert "not logged in" in out.results[0].error


def test_sonnet_handles_nonzero_exit():
    proc = _fake_process(_cli_stdout("permission denied", is_error=True), returncode=2)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "invocation_error" in out.results[0].error
    assert "rc=2" in out.results[0].error


def test_sonnet_handles_invalid_cli_wrapper_json():
    proc = _fake_process(b"not-json")

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "json_parse_error" in out.results[0].error


def test_sonnet_handles_invalid_result_json():
    proc = _fake_process(_cli_stdout("I refuse, sorry."))

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "json_parse_error" in out.results[0].error

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
                "short_title_ja": "🌱 自動給水ウィック",
                "summary_ja": "毛細管で水を吸い上げ、詰まりやすい既存品の不満を構造で解決。",
                "opportunity_ja": "月検索 11.8 万・既存品は詰まり不満",
                "next_action_steps_ja": [
                    "Fusion 360 で 60 分・PETG で 30 分プリント・材料費 ¥50",
                    "Etsy で $8 受注生産・利益率 80%・在庫リスク 0",
                    "月 10 件売れたら Printables で STL ファイル販売も追加",
                ],
                "failure_reasons_ja": [
                    "既存品が安く、単体部品では価格差を出しにくい",
                    "フェルト劣化で数週間後のレビューが荒れやすい",
                    "鉢サイズ差で装着できない返品が増えやすい",
                    "過給水の不安があり初心者の購入前離脱が起きる",
                    "写真だけでは仕組みが伝わらず広告費が重くなる",
                ],
                "failure_mitigations_ja": [
                    "対応鉢を絞り、交換フェルト同梱で価値を上げる",
                    "交換キットを用意し、30 日後の導線を作る",
                    "寸法テンプレート画像で購入前確認を促す",
                    "水位目盛りと動画で安全な使い方を明示する",
                    "断面写真と比較画像で仕組みを即理解させる",
                ],
                "confidence_score": 85,
                "confidence_bom": 70,
                "confidence_amazon_gap": 60,
                "diy_friendly": True,
                "diy_print_minutes": 45,
                "diy_material_cost_jpy": 80,
                "diy_required_extras": ["不織布フェルト x 1"],
                "diy_score": 8,
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
    assert out.results[0].short_title_ja == "🌱 自動給水ウィック"
    assert len(out.results[0].next_action_steps_ja) == 3
    assert "Etsy" in out.results[0].next_action_steps_ja[1]
    assert len(out.results[0].failure_reasons_ja) == 5
    assert out.results[0].confidence_score == 85
    assert out.results[0].confidence_bom == 70
    assert out.results[0].confidence_amazon_gap == 60
    assert out.results[0].diy_friendly is True
    assert out.results[0].diy_print_minutes == 45
    assert out.results[0].diy_material_cost_jpy == 80
    assert out.results[0].diy_required_extras == ["不織布フェルト x 1"]
    assert out.results[0].diy_score == 8
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


def test_sonnet_handles_nonzero_exit(monkeypatch):
    async def no_sleep(delay):
        return None

    monkeypatch.setattr(scorer_sonnet.asyncio, "sleep", no_sleep)
    proc = _fake_process(_cli_stdout("permission denied", is_error=True), returncode=2)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "invocation_error" in out.results[0].error
    assert "rc=2" in out.results[0].error


def test_sonnet_retries_nonzero_exit_then_succeeds(monkeypatch):
    async def no_sleep(delay):
        return None

    monkeypatch.setattr(scorer_sonnet.asyncio, "sleep", no_sleep)
    calls = 0
    result = json.dumps([{"patent_id": "8234811", "score": 8}])

    async def fake_runner(argv, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _cli_stdout("temporary failure").decode(), "boom", 1
        return _cli_stdout(result).decode(), "", 0

    out = asyncio.run(scorer_sonnet.score_batch([_patent()], runner=fake_runner))

    assert calls == 2
    assert out.results[0].error is None
    assert out.results[0].score == 8


def test_sonnet_handles_invalid_cli_wrapper_json():
    proc = _fake_process(b"not-json")

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "json_parse_error" in out.results[0].error


def test_sonnet_new_fields_fall_back_when_missing():
    result = json.dumps([{"patent_id": "8234811", "score": 8}])
    proc = _fake_process(_cli_stdout(result))

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    row = out.results[0]
    assert row.error is None
    assert row.short_title_ja == ""
    assert row.summary_ja == ""
    assert row.opportunity_ja == ""
    assert row.next_action_steps_ja == []
    assert row.failure_reasons_ja == []
    assert row.failure_mitigations_ja == []
    assert row.confidence_score is None
    assert row.confidence_bom is None
    assert row.confidence_amazon_gap is None
    assert row.diy_friendly is None
    assert row.diy_print_minutes is None
    assert row.diy_material_cost_jpy is None
    assert row.diy_required_extras == []
    assert row.diy_score is None


def test_sonnet_handles_invalid_result_json():
    proc = _fake_process(_cli_stdout("I refuse, sorry."))

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = asyncio.run(scorer_sonnet.score_batch([_patent()]))

    assert out.results[0].error and "json_parse_error" in out.results[0].error

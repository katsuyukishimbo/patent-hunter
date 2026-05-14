"""Codex scorer tests. The codex subprocess is stubbed."""

from __future__ import annotations

import asyncio
import json

from patent_hunter.scorers import codex as scorer_codex

from tests.conftest import make_patent


def test_codex_parses_subprocess_stdout():
    payload = json.dumps(
        [
            {
                "patent_id": "8234811",
                "score": 7,
                "consumer_viable": True,
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
                "diy_required_extras": [],
                "diy_score": 10,
            }
        ]
    )

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
    assert out.results[0].short_title_ja == "🌱 自動給水ウィック"
    assert len(out.results[0].next_action_steps_ja) == 3
    assert "Etsy" in out.results[0].next_action_steps_ja[1]
    assert len(out.results[0].failure_reasons_ja) == 5
    assert out.results[0].confidence_score == 85
    assert out.results[0].confidence_bom == 70
    assert out.results[0].confidence_amazon_gap == 60
    assert out.results[0].diy_friendly is True
    assert out.results[0].diy_score == 10
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


def test_codex_new_fields_fall_back_when_missing():
    async def fake_runner(argv, timeout):
        return json.dumps([{"patent_id": "8234811", "score": 8}])

    out = asyncio.run(
        scorer_codex.score_batch([make_patent(pid="8234811")], runner=fake_runner)
    )

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

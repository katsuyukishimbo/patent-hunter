"""End-to-end runner test with both scorers stubbed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from patent_hunter.runner import CostBudgetExceededError, RunConfig, run
from patent_hunter.week import IsoWeek

from tests.conftest import make_patent


def _patent(
    pid: str,
    score_pair: tuple[int, int],
    diy_pair: tuple[bool | None, bool | None] = (True, True),
    confidence_pair: tuple[int | None, int | None] = (90, 90),
):
    """Helper: a patent + the scorer outputs we want stubs to return."""
    p = make_patent(pid)
    p._stub_scores = score_pair  # type: ignore[attr-defined]
    p._stub_diy = diy_pair  # type: ignore[attr-defined]
    p._stub_confidence = confidence_pair  # type: ignore[attr-defined]
    return p


def _extract_payload(text):
    """Pull the JSON array out of the user-message string."""
    marker = "PATENTS = "
    idx = text.find(marker)
    assert idx >= 0
    return json.loads(text[idx + len(marker) :])


NEXT_STEPS = [
    "Onshape で 30 分モデリング・PETG で 35 分印刷・材料費 ¥35",
    "Etsy で $8 受注生産・クリックポスト発送で在庫 0",
    "月 10 件売れたら Printables で STL $4 販売も追加",
]

FAILURE_REASONS = [
    "低価格の既存品が多く、単体販売では価格差を出しにくい",
    "耐久テスト不足だと、数週間後のレビュー低下につながりやすい",
    "用途を広げすぎると訴求がぼやけ、購入前に比較負けしやすい",
    "サイズ違いの返品が増えると、少量販売の利益がすぐ消える",
    "写真だけでは構造価値が伝わらず、広告費が重くなりやすい",
]

FAILURE_MITIGATIONS = [
    "用途とサイズを絞り、比較画像で価格差の理由を明確にする",
    "簡易耐久テストを商品画像に載せ、保証範囲も短く明記する",
    "最初は一用途専用で出し、検索語と訴求を細く合わせる",
    "購入前の寸法確認画像を用意し、返品条件を明確にする",
    "断面図と使用動画を先頭に置き、広告前に価値を伝える",
]


def _build_sonnet_runner(scores_by_id):
    async def runner(argv, timeout):
        prompt = argv[argv.index("-p") + 1]
        ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
        out = [
            {
                "patent_id": pid,
                "plain_english": f"Plain for {pid}",
                "consumer_viable": True,
                "bom_estimate": "$1-2",
                "amazon_gap": True,
                "review_signal": "noise",
                "score": scores_by_id[pid][0],
                "short_title_ja": f"🔧 特許{pid}",
                "summary_ja": f"特許{pid}の日本語サマリ。既存品の不満を構造で解決。",
                "opportunity_ja": "月検索 1.0 万・既存品は不満あり",
                "next_action_steps_ja": NEXT_STEPS,
                "failure_reasons_ja": FAILURE_REASONS,
                "failure_mitigations_ja": FAILURE_MITIGATIONS,
                "confidence_score": scores_by_id[pid][3][0],
                "confidence_bom": 82,
                "confidence_amazon_gap": 76,
                "diy_friendly": scores_by_id[pid][2][0],
                "diy_print_minutes": 45,
                "diy_material_cost_jpy": 80,
                "diy_required_extras": [],
                "diy_score": 8,
            }
            for pid in ids
        ]
        return json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": json.dumps(out),
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

    return runner


def _build_sonnet_runner_with_missing(scores_by_id, missing_ids: set[str]):
    async def runner(argv, timeout):
        prompt = argv[argv.index("-p") + 1]
        ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
        out = [
            {
                "patent_id": pid,
                "plain_english": f"Plain for {pid}",
                "consumer_viable": True,
                "bom_estimate": "$1-2",
                "amazon_gap": True,
                "review_signal": "noise",
                "score": scores_by_id[pid][0],
                "short_title_ja": f"🔧 特許{pid}",
                "summary_ja": f"特許{pid}の日本語サマリ。既存品の不満を構造で解決。",
                "opportunity_ja": "月検索 1.0 万・既存品は不満あり",
                "next_action_steps_ja": NEXT_STEPS,
                "failure_reasons_ja": FAILURE_REASONS,
                "failure_mitigations_ja": FAILURE_MITIGATIONS,
                "confidence_score": scores_by_id[pid][3][0],
                "confidence_bom": 82,
                "confidence_amazon_gap": 76,
                "diy_friendly": scores_by_id[pid][2][0],
                "diy_print_minutes": 45,
                "diy_material_cost_jpy": 80,
                "diy_required_extras": [],
                "diy_score": 8,
            }
            for pid in ids
            if pid not in missing_ids
        ]
        return json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": json.dumps(out),
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

    return runner


def _build_codex_runner(scores_by_id):
    async def runner(argv, timeout):
        # The prompt is the last arg.
        prompt = argv[-1]
        ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
        return json.dumps(
            [
                {
                    "patent_id": pid,
                    "plain_english": f"Codex view of {pid}",
                    "consumer_viable": True,
                    "bom_estimate": "$1-2",
                    "amazon_gap": False,
                    "review_signal": "x",
                    "score": scores_by_id[pid][1],
                    "short_title_ja": f"🔧 特許{pid}",
                    "summary_ja": f"Codex 特許{pid}の日本語サマリ。",
                    "opportunity_ja": "月検索 1.0 万・既存品は不満あり",
                    "next_action_steps_ja": NEXT_STEPS,
                    "failure_reasons_ja": FAILURE_REASONS,
                    "failure_mitigations_ja": FAILURE_MITIGATIONS,
                    "confidence_score": scores_by_id[pid][3][1],
                    "confidence_bom": 80,
                    "confidence_amazon_gap": 74,
                    "diy_friendly": scores_by_id[pid][2][1],
                    "diy_print_minutes": 50,
                    "diy_material_cost_jpy": 90,
                    "diy_required_extras": [],
                    "diy_score": 8,
                }
                for pid in ids
            ]
        )

    return runner


def test_runner_adopts_only_when_both_models_pass(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [
        _patent("A", (8, 8)),  # adopted
        _patent("B", (9, 5)),  # rejected (codex low)
        _patent("C", (4, 9)),  # rejected (sonnet low)
        _patent("D", (7, 7)),  # adopted (boundary)
    ]
    scores_by_id = {
        p.patent_id: (*p._stub_scores, p._stub_diy, p._stub_confidence) for p in patents  # type: ignore[attr-defined]
    }

    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)

    log_text = paths["log"].read_text()
    assert "fetched=4" in log_text
    assert "adopted=2" in log_text

    # scores.jsonl has one row per scored patent
    rows = [json.loads(l) for l in paths["scores"].read_text().splitlines()]
    assert len(rows) == 4
    adopted_ids = {r["patent"]["patent_id"] for r in rows if r["adopted"]}
    assert adopted_ids == {"A", "D"}
    assert rows[0]["sonnet"]["next_action_steps_ja"] == NEXT_STEPS

    html = paths["report"].read_text()
    assert "Patent Hunter" in html
    assert "badge-adopted" in html
    assert "🚀 次の一歩" in html
    assert "Onshape で 30 分モデリング" in html
    assert "US A" in html or "USA" in html  # link present


def test_runner_min_confidence_filters_adopted_patents(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [
        _patent("A", (8, 8), confidence_pair=(90, 88)),
        _patent("B", (8, 8), confidence_pair=(79, 90)),
        _patent("C", (8, 8), confidence_pair=(None, 85)),
    ]
    scores_by_id = {
        p.patent_id: (*p._stub_scores, p._stub_diy, p._stub_confidence) for p in patents  # type: ignore[attr-defined]
    }

    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        min_confidence=80,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)

    rows = [json.loads(l) for l in paths["scores"].read_text().splitlines()]
    adopted_ids = {row["patent"]["patent_id"] for row in rows if row["adopted"]}
    assert adopted_ids == {"A", "C"}
    assert "adopted=2" in paths["log"].read_text()
    assert "min_confidence=80" in paths["log"].read_text()
    assert "信頼度 ≥ 80%" in paths["report"].read_text()


def test_runner_handles_zero_fetched(tmp_path: Path):
    week = IsoWeek(2026, 19)
    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        fetched_patents=[],
        sonnet_client=_build_sonnet_runner({}),
        codex_runner=_build_codex_runner({}),
    )
    paths = run(cfg)
    assert paths["report"].exists()
    log_text = paths["log"].read_text()
    assert "fetched=0" in log_text
    assert "adopted=0" in log_text


def test_runner_shows_shortlist_when_nothing_adopted(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [_patent("A", (3, 3)), _patent("B", (4, 5))]
    scores_by_id = {
        p.patent_id: (*p._stub_scores, p._stub_diy, p._stub_confidence) for p in patents  # type: ignore[attr-defined]
    }
    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)
    html = paths["report"].read_text()
    # No adopted badge, but the shortlist rows must still be rendered.
    assert "badge-shortlist" in html
    assert "badge badge-adopted" not in html


def test_runner_diy_only_requires_both_models_to_mark_friendly(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [
        _patent("A", (8, 8), (True, True)),
        _patent("B", (8, 8), (True, False)),
        _patent("C", (8, 8), (None, True)),
        _patent("D", (8, 8), (False, False)),
    ]
    scores_by_id = {
        p.patent_id: (*p._stub_scores, p._stub_diy, p._stub_confidence) for p in patents  # type: ignore[attr-defined]
    }

    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
        diy_only=True,
    )
    paths = run(cfg)

    rows = [json.loads(l) for l in paths["scores"].read_text().splitlines()]
    adopted_ids = {row["patent"]["patent_id"] for row in rows if row["adopted"]}
    assert adopted_ids == {"A"}
    assert "adopted=1" in paths["log"].read_text()
    assert "DIY モード" in paths["report"].read_text()


def test_runner_allows_partial_score_failures(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [_patent(f"P{i:02d}", (8, 8)) for i in range(50)]
    scores_by_id = {
        p.patent_id: (*p._stub_scores, p._stub_diy, p._stub_confidence) for p in patents  # type: ignore[attr-defined]
    }
    missing_ids = {f"P{i:02d}" for i in range(5)}

    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner_with_missing(scores_by_id, missing_ids),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)

    log_text = paths["log"].read_text()
    assert "fetched=50" in log_text
    assert "scored=50" in log_text
    assert "adopted=45" in log_text
    assert "sonnet_errors=5" in log_text
    assert "partial_score_errors" in log_text

    rows = [json.loads(l) for l in paths["scores"].read_text().splitlines()]
    assert sum(1 for row in rows if row["adopted"]) == 45
    assert sum(1 for row in rows if row["sonnet"]["error"]) == 5


def test_runner_budget_exceeded_writes_partial_outputs(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [_patent("A", (8, 8))]
    scores_by_id = {
        p.patent_id: (*p._stub_scores, p._stub_diy, p._stub_confidence) for p in patents  # type: ignore[attr-defined]
    }
    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
        max_cost_usd=0.0001,
    )

    with pytest.raises(CostBudgetExceededError) as excinfo:
        run(cfg)

    paths = excinfo.value.output_paths
    assert paths["report"].exists()
    assert paths["scores"].exists()
    assert paths["log"].exists()
    assert "budget_exceeded" in paths["log"].read_text()
    events = [json.loads(line) for line in paths["events"].read_text().splitlines()]
    assert "budget_exceeded" in {row["event"] for row in events}

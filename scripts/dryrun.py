#!/usr/bin/env python3
"""Stubbed dry-run of the patent_hunter pipeline.

This script does NOT call the network. It feeds the runner a tiny set of
realistic-looking patents (modelled after the three Gipp ``Hit`` examples)
and stubs both scorers so the full pipeline ends in real HTML + JSONL +
log files that you can open in a browser.

Why this exists: the production CLI needs Google credentials, Claude CLI
authentication, and internet access, none of which are required for this
fixture path. The dry-run lets us verify report rendering, JSONL
serialisation, and the "both models score >= threshold" gate locally.

Run with:

    python3 scripts/dryrun.py

Outputs land under ``out/<ISO-week>/``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SP = ROOT / ".venv" / "lib" / "python3.12" / "site-packages"
SRC = ROOT / "src"
sys.path.insert(0, str(SP))
sys.path.insert(0, str(SRC))
os.chdir(ROOT)

from patent_hunter.models import Patent  # noqa: E402
from patent_hunter.runner import RunConfig, run  # noqa: E402
from patent_hunter.week import previous_iso_week  # noqa: E402

FIXTURE_PATENTS = [
    Patent(
        patent_id="8234811",
        title="Self-watering planter insert",
        abstract=(
            "A planter insert with a passive felt wick that delivers water "
            "to a potted plant without electronics, reducing evaporation."
        ),
        grant_date="2012-08-07",
        filing_date="2009-03-12",
        assignee_name="Greenhaus LLC",
        cpc_code="A47G 7/02",
        category="household",
        claim_count=8,
        first_claim="1. A planter insert comprising a felt wick ...",
        google_patents_url="https://patents.google.com/patent/US8234811",
    ),
    Patent(
        patent_id="7918184",
        title="Collapsible pet water bowl with one-hand lock",
        abstract=(
            "A foldable silicone bowl with a single-action snap lock. No "
            "moving parts beyond the hinge. Dishwasher safe."
        ),
        grant_date="2011-04-05",
        filing_date="2008-11-02",
        assignee_name="Pawly Inc",
        cpc_code="A01K 5/01",
        category="pet_products",
        claim_count=6,
        first_claim="1. A pet water bowl comprising a foldable silicone body ...",
        google_patents_url="https://patents.google.com/patent/US7918184",
    ),
    Patent(
        patent_id="7178762",
        title="Adjustable cable management clip with ratchet jaws",
        abstract=(
            "A cable clip with self-variable ratchet jaws that accommodate "
            "cable diameters from 2mm to 12mm without tools."
        ),
        grant_date="2007-02-20",
        filing_date="2005-07-15",
        assignee_name="Cordwise Corp",
        cpc_code="H02G 3/30",
        category="cable_management",
        claim_count=11,
        first_claim="1. A cable management clip comprising a ratchet jaw ...",
        google_patents_url="https://patents.google.com/patent/US7178762",
    ),
    Patent(
        patent_id="9000000",
        title="Disposable single-use kitchen sponge",
        abstract="A sponge.",
        grant_date="2014-05-01",
        filing_date="2011-02-01",
        assignee_name="Acme",
        cpc_code="A47L 17/08",
        category="household",
        claim_count=3,
        first_claim="1. A sponge.",
        google_patents_url="https://patents.google.com/patent/US9000000",
    ),
]

# Scores the stubs will return: (sonnet, codex). 4th one is a "bad" patent.
STUB_SCORES = {
    "8234811": (8, 8),  # adopted
    "7918184": (9, 7),  # adopted, but not DIY-friendly
    "7178762": (7, 7),  # adopted (boundary) and DIY-friendly
    "9000000": (3, 4),  # shortlist only
}

STUB_DETAILS = {
    "8234811": {
        "short_title_ja": "🌱 自動給水ウィック",
        "summary_ja": "毛細管で水を吸い上げ、詰まりやすい既存プランターの不満を構造で解決。",
        "opportunity_ja": "月検索 11.8 万・既存品は詰まり不満",
        "next_action_steps_ja": [
            "Fusion 360 で 45 分モデリング・PLA で 40 分印刷・材料費 ¥80",
            "Etsy で $12 受注生産・フェルト同梱・在庫 0 で開始",
            "月 10 件売れたら Printables で STL $4 販売も追加",
        ],
        "failure_reasons_ja": [
            "既存の自動給水鉢が安く、部品単体では価格差を出しにくい",
            "フェルトのカビや劣化で、数週間後のレビューが荒れやすい",
            "鉢サイズが合わない返品が多く、汎用品として売りにくい",
            "給水量の調整が難しく、初心者ほど根腐れ不安で迷いやすい",
            "写真だけでは仕組みが伝わらず、広告費が先に重くなる",
        ],
        "failure_mitigations_ja": [
            "対応鉢を 3 サイズに絞り、交換フェルト同梱で価値を上げる",
            "抗菌フェルトの交換キットを用意し、30 日後の導線を作る",
            "寸法テンプレート画像を商品ページに置き、購入前確認を促す",
            "水位目盛りと動画を付け、根腐れしない使い方を明示する",
            "断面写真と比較画像を 1 枚目に置き、仕組みを即理解させる",
        ],
        "diy_friendly": True,
        "diy_print_minutes": 45,
        "diy_material_cost_jpy": 80,
        "diy_required_extras": ["不織布フェルト x 1"],
        "diy_score": 8,
    },
    "7918184": {
        "short_title_ja": "🐾 片手ロック給水皿",
        "summary_ja": "片手で畳める携帯皿。漏れやすい既存品の不満をロック構造で抑える。",
        "opportunity_ja": "月検索 9.2 万・既存品は漏れ不満",
        "next_action_steps_ja": [
            "弁理士に lapsed 確認を依頼 (¥10k・1 週・任意確認)",
            "Alibaba でシリコン成形 3 社にサンプル発注 ($300・4 週)",
            "漏れテスト OK なら MOQ 1000・$4k 初期で Amazon FBA 直納",
        ],
        "failure_reasons_ja": [
            "折り畳みペット皿は既存品が多く、見た目だけでは埋もれる",
            "食品用シリコン成形が必要で、個人検証の初期費用が重い",
            "ロック部の耐久が弱いと、旅行中の漏れレビューで失速する",
            "犬種や水量でサイズ期待が分かれ、返品理由が散りやすい",
            "ブランド信頼がない新規品は、安全性説明なしだと買われにくい",
        ],
        "failure_mitigations_ja": [
            "片手ロックの漏れ比較動画を先頭に置き、機能差を明確にする",
            "最初は 3 社サンプルのみで止め、金型前に需要を検証する",
            "開閉 500 回テスト動画と保証文を載せ、耐久不安を下げる",
            "小型犬向けなど用途を絞り、容量と寸法を画像で大きく出す",
            "BPA フリー証明と洗浄方法を明記し、安全性の疑問を潰す",
        ],
        "diy_friendly": False,
        "diy_print_minutes": 120,
        "diy_material_cost_jpy": 220,
        "diy_required_extras": ["食品用シリコン"],
        "diy_score": 3,
    },
    "7178762": {
        "short_title_ja": "🔌 ラチェット配線留め",
        "summary_ja": "径違いのケーブルを工具なしで固定し、外れやすい既存クリップを爪構造で解決。",
        "opportunity_ja": "月検索 6.4 万・既存品は外れ不満",
        "next_action_steps_ja": [
            "Onshape で 30 分モデリング・PETG で 35 分印刷・材料費 ¥35",
            "メルカリ Shops で 3 個 ¥780 受注生産・封筒発送で検証",
            "月 20 セット売れたら BOOTH で STL ¥500 販売も追加",
        ],
        "failure_reasons_ja": [
            "ケーブルクリップは低単価品が多く、送料込み利益が薄くなる",
            "ラチェット爪が小さく、FDM だと積層方向で割れやすい",
            "太さ対応を広げすぎると固定力が弱く、用途説明がぼやける",
            "既存の結束バンドで十分と思われ、差別化が伝わりにくい",
            "オフィス用品はまとめ買い前提で、単品販売の回転が鈍い",
        ],
        "failure_mitigations_ja": [
            "3 個セットと封筒発送に固定し、送料込みでも粗利を残す",
            "爪方向を積層に合わせて設計し、PETG で曲げ試験を載せる",
            "2-6mm 専用など用途を絞り、固定力の強い範囲だけ売る",
            "再利用できる点を結束バンド比較で見せ、捨てない価値を出す",
            "在宅デスク配線セットとして束売りし、客単価を上げる",
        ],
        "diy_friendly": True,
        "diy_print_minutes": 35,
        "diy_material_cost_jpy": 35,
        "diy_required_extras": [],
        "diy_score": 10,
    },
    "9000000": {
        "short_title_ja": "🧽 使い捨てスポンジ",
        "summary_ja": "単純なスポンジで差別化が弱く、既存品の衛生不満を構造で解決できない。",
        "opportunity_ja": "月検索 4.1 万・既存品多数",
        "next_action_steps_ja": [
            "弁理士に lapsed 確認 (¥10k・1 週) 後に差別化余地を再確認",
            "Alibaba でスポンジ加工 3 社にサンプル発注 ($200・3 週)",
            "粗利 30% 未満なら MOQ 1000・$2k 投資前に FBA 見送り",
        ],
        "failure_reasons_ja": [
            "単純なスポンジは既存品が安く、機能差を説明しにくい",
            "消耗品なのに差別化が薄く、広告費を回収しにくい",
            "衛生訴求は競合が強く、レビューで優位を作りにくい",
            "配送時のかさばりで、低単価商品の FBA 手数料が重い",
            "特許由来の構造価値が乏しく、模倣防止にもつながらない",
        ],
        "failure_mitigations_ja": [
            "抗菌素材や形状改善がない限り、単体商品化は見送る",
            "広告前に 100 個だけテスト販売し、粗利と反応を測る",
            "衛生比較の根拠を作れない場合は、訴求軸を変えず撤退する",
            "薄型圧縮包装にできる supplier だけを候補に残す",
            "特許ではなくブランド消耗品として成立するか再判定する",
        ],
        "diy_friendly": False,
        "diy_print_minutes": 10,
        "diy_material_cost_jpy": 5,
        "diy_required_extras": ["スポンジ素材"],
        "diy_score": 1,
    },
}

STUB_CONFIDENCE = {
    "8234811": {
        "sonnet": {
            "confidence_score": 88,
            "confidence_bom": 78,
            "confidence_amazon_gap": 72,
        },
        "codex": {
            "confidence_score": 85,
            "confidence_bom": 74,
            "confidence_amazon_gap": 70,
        },
    },
    "7918184": {
        "sonnet": {
            "confidence_score": 76,
            "confidence_bom": 65,
            "confidence_amazon_gap": 58,
        },
        "codex": {
            "confidence_score": 82,
            "confidence_bom": 68,
            "confidence_amazon_gap": 62,
        },
    },
    "7178762": {
        "sonnet": {
            "confidence_score": 80,
            "confidence_bom": 72,
            "confidence_amazon_gap": 61,
        },
        "codex": {
            "confidence_score": 79,
            "confidence_bom": 70,
            "confidence_amazon_gap": 58,
        },
    },
    "9000000": {
        "sonnet": {
            "confidence_score": 90,
            "confidence_bom": 80,
            "confidence_amazon_gap": 85,
        },
        "codex": {
            "confidence_score": 88,
            "confidence_bom": 78,
            "confidence_amazon_gap": 82,
        },
    },
}


def _extract_payload(text: str):
    marker = "PATENTS = "
    idx = text.find(marker)
    return json.loads(text[idx + len(marker) :])


async def _fake_sonnet_runner(argv, timeout):
    prompt = argv[argv.index("-p") + 1]
    ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
    items = []
    for pid in ids:
        score = STUB_SCORES.get(pid, (5, 5))[0]
        items.append(
            {
                "patent_id": pid,
                "plain_english": f"Sonnet plain-English summary for US{pid}.",
                "consumer_viable": score >= 6,
                "bom_estimate": "$1.60-2.10",
                "amazon_gap": score >= 7,
                "review_signal": "competing products fail in 2 weeks",
                "score": score,
                **STUB_DETAILS[pid],
                **STUB_CONFIDENCE[pid]["sonnet"],
            }
        )
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(items),
            "total_cost_usd": 0.0135,
            "usage": {"input_tokens": 1500, "output_tokens": 600},
        }
    )


async def _fake_codex_runner(argv, timeout):
    prompt = argv[-1]
    ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
    items = []
    for pid in ids:
        score = STUB_SCORES.get(pid, (5, 5))[1]
        items.append(
            {
                "patent_id": pid,
                "plain_english": f"Codex view: US{pid} is a simple mechanism.",
                "consumer_viable": score >= 6,
                "bom_estimate": "$1.40-1.90",
                "amazon_gap": score >= 7,
                "review_signal": "unknown",
                "score": score,
                **STUB_DETAILS[pid],
                **STUB_CONFIDENCE[pid]["codex"],
            }
        )
    return json.dumps(items)


def main():
    parser = argparse.ArgumentParser(description="Run Patent Hunter with fixture data.")
    parser.add_argument(
        "--diy-only",
        action="store_true",
        help="Adopt only fixture patents both model stubs mark as 3D-printable.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=0,
        help="Minimum confidence_score required from both fixture scorers.",
    )
    args = parser.parse_args()

    week = previous_iso_week()
    cfg = RunConfig(
        week=week,
        out_dir=ROOT / "out",
        score_threshold=7,
        max_per_category=10,
        top_n=10,
        fetched_patents=FIXTURE_PATENTS,
        sonnet_client=_fake_sonnet_runner,
        codex_runner=_fake_codex_runner,
        diy_only=args.diy_only,
        min_confidence=args.min_confidence,
    )
    paths = run(cfg)
    print("[dryrun] week    :", week.label)
    print("[dryrun] report  :", paths["report"])
    print("[dryrun] scores  :", paths["scores"])
    print("[dryrun] log     :", paths["log"])
    print()
    print("--- run.log ---")
    print(paths["log"].read_text())


if __name__ == "__main__":
    main()

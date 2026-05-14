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
        "diy_friendly": False,
        "diy_print_minutes": 10,
        "diy_material_cost_jpy": 5,
        "diy_required_extras": ["スポンジ素材"],
        "diy_score": 1,
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

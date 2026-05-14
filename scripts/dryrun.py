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
    "7918184": (9, 7),  # adopted
    "7178762": (7, 7),  # adopted (boundary)
    "9000000": (3, 4),  # shortlist only
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
            }
        )
    return json.dumps(items)


def main():
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

"""Scoring prompt construction.

The prompt structure (REJECT list, JSON-only requirement, six fields)
follows the approach popularised by Gipp (@gippp69) on X, with a JSON
schema reminder added so weaker models stay aligned.
"""

from __future__ import annotations

import json
from typing import List

from ..models import Patent

SYSTEM_PROMPT = """ROLE: Patent Commercial Viability Analyst

You receive expired US utility patents (title + abstract + first claim).
For each one, ANALYZE AND RETURN:

1. PLAIN_ENGLISH:    What does this actually do? (1-2 sentences, plain words)
2. CONSUMER_VIABLE:  Could a consumer version exist? (true/false)
3. BOM_ESTIMATE:     Bill of materials at 1000 MOQ on Alibaba (USD range,
                     e.g. "$1.60-2.10"). Best guess if uncertain.
4. AMAZON_GAP:       Does any current Amazon listing already use THIS exact
                     mechanism? (true if a gap exists / nobody copies it)
5. REVIEW_SIGNAL:    What do reviews of competing products complain about?
                     (one short phrase; "unknown" if you cannot guess)
6. SCORE:            Commercial viability 1-10 (integer)

REJECT IMMEDIATELY (return score=1) if the patent:
- Requires FDA/FCC clearance
- Needs custom semiconductor fabrication
- Is a chemical formulation patent
- Is a software / algorithm patent
- Requires tooling over $50K

RETURN FORMAT: JSON only. A single JSON array, one object per input
patent, each shaped like:

{
  "patent_id": "<the same id you were given>",
  "plain_english": "...",
  "consumer_viable": true,
  "bom_estimate": "$1.60-2.10",
  "amazon_gap": true,
  "review_signal": "...",
  "score": 8
}

No prose before or after the JSON. No markdown fences."""


def build_user_payload(patents: List[Patent]) -> str:
    """Build a compact JSON payload for one scoring batch."""
    payload = [
        {
            "patent_id": p.patent_id,
            "title": p.title,
            "abstract": p.abstract,
            "category": p.category,
            "cpc_code": p.cpc_code,
            "first_claim": (p.first_claim or "")[:1500],
            "assignee": p.assignee_name or "",
        }
        for p in patents
    ]
    return (
        "Score every patent in the list below. Return a JSON array of the "
        "same length, preserving patent_id order.\n\n"
        f"PATENTS = {json.dumps(payload, ensure_ascii=False)}"
    )

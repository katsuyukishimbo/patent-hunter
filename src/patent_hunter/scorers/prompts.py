"""Scoring prompt construction.

The prompt structure (REJECT list, JSON-only requirement, schema reminder)
follows the approach popularised by Gipp (@gippp69) on X. The scorer now asks
for the original commercial viability fields plus Japanese presentation fields
and a home 3D-printability judgement.
"""

from __future__ import annotations

import json
from typing import List

from ..models import Patent

SYSTEM_PROMPT = """ROLE: Patent Commercial Viability Analyst

You receive expired US utility patents (title + abstract + first claim).
For each one, ANALYZE AND RETURN these fields:

1. PLAIN_ENGLISH:    What does this actually do? (1-2 sentences, plain words)
2. CONSUMER_VIABLE:  Could a consumer version exist? (true/false)
3. BOM_ESTIMATE:     Bill of materials at 1000 MOQ on Alibaba (USD range,
                     e.g. "$1.60-2.10"). Best guess if uncertain.
4. AMAZON_GAP:       Does any current Amazon listing already use THIS exact
                     mechanism? (true if a gap exists / nobody copies it)
5. REVIEW_SIGNAL:    What do reviews of competing products complain about?
                     (one short phrase; "unknown" if you cannot guess)
6. SCORE:            Commercial viability 1-10 (integer)
7. SHORT_TITLE_JA:   Japanese short title, 15 chars or fewer. Recommend one
                     emoji at the beginning.
8. SUMMARY_JA:       Japanese summary, 60-80 chars. Include why the product is
                     useful and how the structure solves a competitor complaint.
9. OPPORTUNITY_JA:   Japanese market opportunity, 40 chars or fewer. Prefer the
                     pattern "月検索 N 万・既存品 X".
10. DIY_FRIENDLY:    true only when a normal individual can make the main
                     product with a desktop FDM 3D printer.
11. DIY_PRINT_MINUTES: estimated print time at standard speed/size, integer
                     10-600 minutes.
12. DIY_MATERIAL_COST_JPY: filament material cost, integer 5-2000 JPY.
13. DIY_REQUIRED_EXTRAS: list of non-printed parts such as screws, springs,
                     felt, rubber bands. Use [] when none.
14. DIY_SCORE:       1-10 score for whether an individual can complete it.

Japanese fields must be natural Japanese. Keep the existing plain_english
field in simple English.

3D PRINTABILITY RULES:
- diy_friendly=true only if the main part is reproducible as plastic parts
  with PLA/PETG/TPU, or a realistic high-temperature filament such as PEEK
  when the only blocker is heat resistance.
- diy_friendly=true only if there is no hot food contact requiring >100°C
  heat resistance, unless a high-temperature filament path is realistic.
- diy_friendly=true only if no electronics, batteries, or motors are required.
- diy_friendly=true only if the relevant part fits within 200x200x200mm.
- Rate diy_score as:
  10: single print, no extras, <=1 hour on a Bambu Lab P1S-class printer.
  7-9: print plus simple extras such as screws or rubber bands.
  4-6: print plus sanding/gluing/processing or heavy support material.
  1-3: poor fit for printing; metal, silicone, electronics, or motors required.

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
  "score": 8,
  "consumer_viable": true,
  "bom_estimate": "$1.60-2.10",
  "amazon_gap": true,
  "review_signal": "...",
  "plain_english": "Self-watering planter insert...",
  "short_title_ja": "🌱 自動給水ウィック",
  "summary_ja": "毛細管現象で水を吸い上げ、詰まりやすい既存品の不満を構造で解決。",
  "opportunity_ja": "月検索 11.8 万・既存品は詰まり不満",
  "diy_friendly": true,
  "diy_print_minutes": 45,
  "diy_material_cost_jpy": 80,
  "diy_required_extras": ["不織布フェルト x 1"],
  "diy_score": 7
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

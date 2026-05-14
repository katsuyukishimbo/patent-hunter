"""CPC (Cooperative Patent Classification) code prefixes per target category.

Why CPC prefixes (not full codes):
  Google Patents BigQuery can apply STARTS_WITH over CPC codes cheaply, so a
  4-character prefix gives wide-but-cheap recall. The LLM scorer is the
  precision filter; we only need decent recall here.

Sources used to pick these codes:
  - kitchen:  A47J ("kitchen equipment, coffee mills, ..."), A47G (household
              or table equipment, picnic gear), B65D (containers / lids that
              are largely kitchen storage), F24C (domestic cooking ranges).
  - pet_products: A01K (animal husbandry, including pet care / leashes /
                  bowls / litter boxes / aquariums).
  - cable_management: H02G (installation of electric cables / wires),
                      F16L (pipes / hose / cable clips, often used for
                      cable channels), H01R (electric connectors -- only
                      mechanical-clip parts survive the LLM filter).
  - household: A47B (tables / desks / drawers), A47C (chairs / sofas),
               A47G (household / table equipment), A47L (domestic cleaning),
               B25H (workshop equipment for home use).

These are deliberately *broad*; the Sonnet/Codex scorer eliminates noise
(chemical / software / FDA-regulated patents) downstream.
"""

from __future__ import annotations

from typing import Dict, List

CATEGORY_CPC_PREFIXES: Dict[str, List[str]] = {
    "kitchen": ["A47J", "A47G", "B65D", "F24C"],
    "pet_products": ["A01K"],
    "cable_management": ["H02G", "F16L", "H01R"],
    "household": ["A47B", "A47C", "A47L", "B25H"],
}


def all_prefixes() -> List[str]:
    """Flat list of every CPC prefix we care about (de-duplicated)."""
    seen: List[str] = []
    for prefixes in CATEGORY_CPC_PREFIXES.values():
        for code in prefixes:
            if code not in seen:
                seen.append(code)
    return seen


def category_of(cpc_code: str) -> str | None:
    """Return the first category whose prefix matches `cpc_code`, else None.

    Matching is prefix-based (case-insensitive). A CPC subclass like
    "A47J 27/00" matches the "A47J" prefix.
    """
    if not cpc_code:
        return None
    upper = cpc_code.upper()
    for category, prefixes in CATEGORY_CPC_PREFIXES.items():
        for prefix in prefixes:
            if upper.startswith(prefix):
                return category
    return None

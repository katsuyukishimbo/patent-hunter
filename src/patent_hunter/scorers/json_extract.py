"""Tolerant JSON-array extraction from LLM output.

LLMs sometimes wrap JSON in markdown fences or add a sentence of preface
even when told not to. We strip the noise and return a Python list.

The only contract: input was supposed to be a JSON array of objects.
Anything else raises ValueError so the caller can log + skip the batch.
"""

from __future__ import annotations

import json
import re
from typing import Any, List

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def extract_json_array(text: str) -> List[Any]:
    """Return the first JSON array found in `text`.

    Strategy:
      1. Strip markdown fences if present.
      2. Try a direct json.loads -- works when the model obeyed.
      3. Otherwise locate the first '[' and matching ']' by bracket-counting
         (respecting strings + escapes) and json.loads that slice.
    """
    if not text:
        raise ValueError("empty text")

    cleaned = _FENCE_RE.sub("", text).strip()

    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, list):
            return loaded
        raise ValueError(f"top-level JSON was {type(loaded).__name__}, expected list")
    except json.JSONDecodeError:
        pass  # fall through to bracket extraction

    start = cleaned.find("[")
    if start == -1:
        raise ValueError("no '[' in response")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                slice_ = cleaned[start : i + 1]
                loaded = json.loads(slice_)
                if not isinstance(loaded, list):
                    raise ValueError("bracket slice was not a list")
                return loaded
    raise ValueError("unbalanced brackets in response")


def optional_bool(v: Any) -> bool | None:
    """Return a tolerant bool for LLM JSON fields, or None when absent/unknown."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        lo = v.strip().lower()
        if lo in {"true", "yes", "y", "1"}:
            return True
        if lo in {"false", "no", "n", "0"}:
            return False
    return None


def optional_int(
    value: Any, *, min_value: int | None = None, max_value: int | None = None
) -> int | None:
    """Return an int bounded to the requested range, or None when invalid."""
    if value is None or value == "":
        return None
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if min_value is not None:
        out = max(min_value, out)
    if max_value is not None:
        out = min(max_value, out)
    return out


def string_list(value: Any) -> list[str]:
    """Normalize optional LLM list fields without rejecting old responses."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return []


def score_result_kwargs(obj: dict[str, Any], patent_id: str, raw_text: str) -> dict[str, Any]:
    """Coerce a scorer JSON object into ScoreResult keyword arguments.

    Older model responses do not contain the Japanese or DIY fields. Those
    fields intentionally fall back to "", None, or [] here so historical
    fixtures and persisted JSON remain readable.
    """
    score = optional_int(obj.get("score"), min_value=0, max_value=10) or 0
    return {
        "patent_id": str(obj.get("patent_id") or patent_id),
        "plain_english": str(obj.get("plain_english") or ""),
        "short_title_ja": str(obj.get("short_title_ja") or ""),
        "summary_ja": str(obj.get("summary_ja") or ""),
        "opportunity_ja": str(obj.get("opportunity_ja") or ""),
        "consumer_viable": optional_bool(obj.get("consumer_viable")),
        "bom_estimate": str(obj.get("bom_estimate") or ""),
        "amazon_gap": optional_bool(obj.get("amazon_gap")),
        "review_signal": str(obj.get("review_signal") or ""),
        "score": score,
        "diy_friendly": optional_bool(obj.get("diy_friendly")),
        "diy_print_minutes": optional_int(
            obj.get("diy_print_minutes"), min_value=10, max_value=600
        ),
        "diy_material_cost_jpy": optional_int(
            obj.get("diy_material_cost_jpy"), min_value=5, max_value=2000
        ),
        "diy_required_extras": string_list(obj.get("diy_required_extras")),
        "diy_score": optional_int(obj.get("diy_score"), min_value=1, max_value=10),
        "raw": raw_text,
    }

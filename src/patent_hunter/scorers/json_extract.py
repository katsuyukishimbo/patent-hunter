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

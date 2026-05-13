"""Anthropic (Sonnet) scorer.

The official anthropic Python SDK is synchronous-only on the simple call
path, so we wrap the blocking call in `asyncio.to_thread` to compose with
the Codex scorer in `runner.py`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from .json_extract import extract_json_array
from .prompts import SYSTEM_PROMPT, build_user_payload
from ..models import Patent, ScoreResult

logger = logging.getLogger(__name__)

# Sonnet 4.6 public pricing (per https://docs.anthropic.com/, as of 2026-05).
# These are the constants the scorer uses to *estimate* spend for run.log.
# They are intentionally pessimistic; real billing wins.
SONNET_INPUT_USD_PER_MTOK = 3.0
SONNET_OUTPUT_USD_PER_MTOK = 15.0


@dataclass
class SonnetScoreBatch:
    results: List[ScoreResult]
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _result_from_json(obj: dict, patent_id: str, raw_text: str) -> ScoreResult:
    """Build a ScoreResult from one decoded JSON object."""
    score_raw = obj.get("score", 0)
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0
    return ScoreResult(
        patent_id=str(obj.get("patent_id") or patent_id),
        model="sonnet",
        plain_english=str(obj.get("plain_english") or ""),
        consumer_viable=_optional_bool(obj.get("consumer_viable")),
        bom_estimate=str(obj.get("bom_estimate") or ""),
        amazon_gap=_optional_bool(obj.get("amazon_gap")),
        review_signal=str(obj.get("review_signal") or ""),
        score=max(0, min(10, score)),
        raw=raw_text,
    )


def _optional_bool(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        lo = v.strip().lower()
        if lo in {"true", "yes", "y", "1"}:
            return True
        if lo in {"false", "no", "n", "0"}:
            return False
    return None


def _build_client():
    """Build an anthropic.Anthropic() instance lazily.

    Importing inside the function keeps `pytest` happy even without the
    package installed; tests use the injectable client interface below.
    """
    import anthropic  # local import on purpose

    return anthropic.Anthropic()


def score_batch_sync(
    patents: List[Patent],
    *,
    client=None,
    model: Optional[str] = None,
) -> SonnetScoreBatch:
    """Score one batch synchronously. Used directly by tests."""
    if not patents:
        return SonnetScoreBatch(results=[], input_tokens=0, output_tokens=0, cost_usd=0.0)

    client = client or _build_client()
    model_id = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    user_text = build_user_payload(patents)

    logger.info("Sonnet: scoring batch of %d patents with model=%s", len(patents), model_id)
    message = client.messages.create(
        model=model_id,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )

    # The SDK returns a content list of blocks. Join text blocks.
    text = ""
    for block in getattr(message, "content", []) or []:
        # Each block has .type and .text on the SDK; on dicts it has ["type"].
        b_text = getattr(block, "text", None)
        if b_text is None and isinstance(block, dict):
            b_text = block.get("text")
        if b_text:
            text += b_text

    usage = getattr(message, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cost_usd = (
        input_tokens / 1_000_000 * SONNET_INPUT_USD_PER_MTOK
        + output_tokens / 1_000_000 * SONNET_OUTPUT_USD_PER_MTOK
    )

    by_id = {p.patent_id: p for p in patents}
    results: List[ScoreResult] = []
    try:
        items = extract_json_array(text)
    except ValueError as exc:
        logger.warning("Sonnet returned non-JSON output: %s", exc)
        for p in patents:
            results.append(
                ScoreResult(
                    patent_id=p.patent_id,
                    model="sonnet",
                    raw=text,
                    error=f"json_parse_error: {exc}",
                )
            )
        return SonnetScoreBatch(
            results=results,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    seen: set[str] = set()
    for obj in items:
        if not isinstance(obj, dict):
            continue
        pid = str(obj.get("patent_id") or "")
        if pid not in by_id:
            # Some models echo their own id; try to align by position later.
            continue
        results.append(_result_from_json(obj, pid, text))
        seen.add(pid)

    # Fill in any missing patents so the runner can match 1:1.
    for p in patents:
        if p.patent_id not in seen:
            results.append(
                ScoreResult(
                    patent_id=p.patent_id,
                    model="sonnet",
                    raw=text,
                    error="missing_from_batch_response",
                )
            )

    return SonnetScoreBatch(
        results=results,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


async def score_batch(
    patents: List[Patent],
    *,
    client=None,
    model: Optional[str] = None,
) -> SonnetScoreBatch:
    """Async wrapper around the blocking SDK call."""
    return await asyncio.to_thread(score_batch_sync, patents, client=client, model=model)

"""Discord webhook notification for Phase 2 weekly summaries.

This is a core Phase 2 feature for posting read-only notifications after a
weekly run. It intentionally only sends one-way webhook notifications; Discord
Bot, Interaction, and Approval Gate workflows belong to Phase 3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from patent_hunter.models import ScoredPatent

logger = logging.getLogger(__name__)

DISCORD_MAX_FIELDS = 25
DISCORD_FIELD_NAME_MAX = 256
DISCORD_FIELD_VALUE_MAX = 1024
DISCORD_EMBED_TOTAL_MAX = 6000
DEFAULT_USD_JPY_RATE = 150.0


def _truncate(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"[:max_chars]
    return text[: max_chars - 1] + "…"


def _google_patents_url(patent_id: str) -> str:
    return f"https://patents.google.com/patent/{patent_id.replace('-', '')}"


def usd_range_to_jpy(s: str, rate: float = DEFAULT_USD_JPY_RATE) -> str:
    """Convert a loose USD BOM range like "$1.60-2.10" into a JPY range."""
    env_rate = os.environ.get("USD_JPY_RATE")
    if env_rate:
        try:
            rate = float(env_rate)
        except ValueError:
            pass

    amounts = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", s or "")]
    if not amounts:
        return "不明"
    yen = [int(round(amount * rate)) for amount in amounts[:2]]
    if len(yen) == 1:
        return f"{yen[0]} 円"
    return f"{yen[0]}-{yen[1]} 円"


def _preferred_ja(scored: ScoredPatent, attr: str, fallback: str = "") -> str:
    sonnet_value = getattr(scored.sonnet, attr, "")
    codex_value = getattr(scored.codex, attr, "")
    return str(sonnet_value or codex_value or fallback)


def _preferred_bom(scored: ScoredPatent) -> str:
    return scored.sonnet.bom_estimate or scored.codex.bom_estimate or "不明"


def _diy_badge(scored: ScoredPatent) -> str:
    source = scored.sonnet if scored.sonnet.diy_friendly else scored.codex
    if source.diy_friendly is not True:
        return ""
    minutes = source.diy_print_minutes if source.diy_print_minutes is not None else "?"
    cost = (
        source.diy_material_cost_jpy
        if source.diy_material_cost_jpy is not None
        else "?"
    )
    return f"🔧 個人 3D プリント OK · {minutes}分 · ¥{cost}"


def _preferred_next_steps(scored: ScoredPatent) -> list[str]:
    """Return a display-ready three-step action list when either scorer has one."""
    fallback: list[str] = []
    for result in (scored.sonnet, scored.codex):
        steps = [
            str(step).strip()
            for step in result.next_action_steps_ja
            if str(step).strip()
        ]
        if len(steps) >= 3:
            return steps[:3]
        if steps and not fallback:
            fallback = steps
    return fallback[:3]


def _preferred_failure_reasons(scored: ScoredPatent) -> list[str]:
    """Return display-ready failure reasons from the first scorer that has them."""
    fallback: list[str] = []
    for result in (scored.sonnet, scored.codex):
        reasons = [
            str(reason).strip()
            for reason in result.failure_reasons_ja
            if str(reason).strip()
        ]
        if len(reasons) >= 3:
            return reasons[:3]
        if reasons and not fallback:
            fallback = reasons
    return fallback[:3]


def _min_confidence(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return min(present)


def _confidence_badge(scored: ScoredPatent) -> str:
    score = _min_confidence(
        [scored.sonnet.confidence_score, scored.codex.confidence_score]
    )
    bom = _min_confidence([scored.sonnet.confidence_bom, scored.codex.confidence_bom])
    gap = _min_confidence(
        [scored.sonnet.confidence_amazon_gap, scored.codex.confidence_amazon_gap]
    )
    if score is None and bom is None and gap is None:
        return ""

    def fmt(value: int | None) -> str:
        return "?" if value is None else f"{value}%"

    return f"🎯 信頼度: score {fmt(score)} / BOM {fmt(bom)} / gap {fmt(gap)}"


def _field_for_patent(index: int, scored: ScoredPatent) -> dict[str, Any]:
    patent = scored.patent
    title = _preferred_ja(scored, "short_title_ja", patent.title)
    score = f"{scored.consensus_score:g}"
    name = _truncate(
        f"#{index + 1} {title} (スコア {score})",
        DISCORD_FIELD_NAME_MAX,
    )
    summary = _preferred_ja(scored, "summary_ja", scored.sonnet.plain_english)
    opportunity = _preferred_ja(scored, "opportunity_ja", "不明")
    bom = _preferred_bom(scored)
    lines = [
        _truncate(summary or "概要なし", 90),
        f"💡 売り筋: {opportunity or '不明'}",
        f"🏭 製造原価: {bom} (≒ ¥{usd_range_to_jpy(bom)})",
    ]
    confidence_badge = _confidence_badge(scored)
    if confidence_badge:
        lines.append(confidence_badge)
    badge = _diy_badge(scored)
    if badge:
        lines.append(badge)
    failure_reasons = _preferred_failure_reasons(scored)
    if failure_reasons:
        lines.append("⚠️ 失敗想定 (Top 3):")
        lines.extend(f"・{_truncate(reason, 80)}" for reason in failure_reasons)
    next_steps = _preferred_next_steps(scored)
    if next_steps:
        lines.append("🚀 次の一歩:")
        lines.extend(
            f"{step_index}. {_truncate(step, 120)}"
            for step_index, step in enumerate(next_steps, start=1)
        )
    lines.append(f"🔗 [特許リンク]({_google_patents_url(patent.patent_id)})")
    value = "\n".join(lines)
    return {
        "name": name,
        "value": _truncate(value, DISCORD_FIELD_VALUE_MAX),
        "inline": False,
    }


def _embed_char_count(embed: dict[str, Any]) -> int:
    total = len(embed.get("title", ""))
    total += len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", ""))
        total += len(field.get("value", ""))
    return total


def _append_field_with_total_limit(embed: dict[str, Any], field: dict[str, Any]) -> bool:
    fields = embed["fields"]
    candidate = {**embed, "fields": [*fields, field]}
    if _embed_char_count(candidate) <= DISCORD_EMBED_TOTAL_MAX:
        fields.append(field)
        return True

    remaining_for_value = (
        DISCORD_EMBED_TOTAL_MAX
        - _embed_char_count(embed)
        - len(field.get("name", ""))
    )
    if remaining_for_value <= 0:
        return False

    trimmed = {
        **field,
        "value": _truncate(
            field.get("value", ""),
            min(DISCORD_FIELD_VALUE_MAX, remaining_for_value),
        ),
    }
    candidate = {**embed, "fields": [*fields, trimmed]}
    if trimmed["value"] and _embed_char_count(candidate) <= DISCORD_EMBED_TOTAL_MAX:
        fields.append(trimmed)
        return True
    return False


def format_embed(
    week_label: str, adopted: list[ScoredPatent], top_n: int = 10
) -> dict[str, Any]:
    """Return the Discord webhook JSON payload for top adopted patents."""

    run_ts = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    embed: dict[str, Any] = {
        "title": f"📋 Patent Hunter — Week {week_label} ({len(adopted)} 件採用)",
        "description": f"{len(adopted)} 件が両モデルでスコア 7+ で合意",
        "color": 0x198754,
        "fields": [],
        "footer": {"text": f"Run: {run_ts} JST"},
    }

    limit = min(max(top_n, 0), DISCORD_MAX_FIELDS)
    for index, scored in enumerate(adopted[:limit]):
        if not _append_field_with_total_limit(embed, _field_for_patent(index, scored)):
            break

    return {"username": "Patent Hunter", "embeds": [embed]}


async def send_top_patents(
    webhook_url: str,
    week_label: str,
    adopted: list[ScoredPatent],
    top_n: int = 10,
    timeout_seconds: float = 10.0,
) -> bool:
    """Post top adopted patents to Discord, returning False on any failure."""

    if not webhook_url:
        return False

    payload = format_embed(week_label, adopted, top_n)
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(webhook_url, json=payload)
            if 200 <= response.status_code < 300:
                return True
            logger.warning(
                "Discord webhook returned non-2xx status: status_code=%s attempt=%s",
                response.status_code,
                attempt + 1,
            )
        except Exception as exc:  # noqa: BLE001 - notification is best-effort.
            logger.warning(
                "Discord webhook post failed: attempt=%s error=%s",
                attempt + 1,
                exc,
            )
        if attempt == 0:
            await asyncio.sleep(0)
    return False

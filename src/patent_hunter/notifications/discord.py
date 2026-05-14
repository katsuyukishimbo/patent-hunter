"""Discord webhook notification for Phase 2 weekly summaries.

This is a core Phase 2 feature for posting read-only notifications after a
weekly run. It intentionally only sends one-way webhook notifications; Discord
Bot, Interaction, and Approval Gate workflows belong to Phase 3.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from patent_hunter.models import ScoredPatent

logger = logging.getLogger(__name__)

DISCORD_MAX_FIELDS = 25
DISCORD_FIELD_NAME_MAX = 256
DISCORD_FIELD_VALUE_MAX = 1024
DISCORD_EMBED_TOTAL_MAX = 6000


def _truncate(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"[:max_chars]
    return text[: max_chars - 1] + "…"


def _google_patents_url(patent_id: str) -> str:
    return f"https://patents.google.com/patent/{patent_id.replace('-', '')}"


def _field_for_patent(index: int, scored: ScoredPatent) -> dict[str, Any]:
    patent = scored.patent
    title = _truncate(patent.title, 80)
    name = _truncate(f"#{index + 1} {title}", DISCORD_FIELD_NAME_MAX)
    plain_english = _truncate(scored.sonnet.plain_english, 200)
    value = (
        f"[`{patent.patent_id}`]({_google_patents_url(patent.patent_id)})"
        f" · CPC `{patent.cpc_code}` · {patent.category}\n"
        f"BOM: {scored.sonnet.bom_estimate} · "
        f"Consensus: **{scored.consensus_score}** "
        f"(Sonnet {scored.sonnet.score} / Codex {scored.codex.score})\n"
        f"_{plain_english}_"
    )
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

    run_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    embed: dict[str, Any] = {
        "title": f"📋 Patent Hunter — Week {week_label}",
        "description": f"**{len(adopted)} patents** scored 7+ by both Sonnet and Codex.",
        "color": 0x198754,
        "fields": [],
        "footer": {"text": f"Run: {run_ts}"},
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

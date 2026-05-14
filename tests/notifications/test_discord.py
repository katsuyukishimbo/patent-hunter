"""Discord webhook notification tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from patent_hunter.models import ScoredPatent, ScoreResult
from patent_hunter.notifications import discord
from patent_hunter.runner import RunConfig, run
from patent_hunter.week import IsoWeek

from tests.conftest import make_patent


def _scored(
    pid: str = "US-1234567-A",
    *,
    title: str | None = None,
    plain_english: str = "A simple consumer product with obvious sourcing paths.",
    adopted: bool = True,
) -> ScoredPatent:
    patent = make_patent(pid, title=title)
    sonnet = ScoreResult(
        patent_id=pid,
        model="sonnet",
        plain_english=plain_english,
        consumer_viable=True,
        bom_estimate="$1-2",
        amazon_gap=True,
        review_signal="reviews mention durability",
        score=8,
    )
    codex = ScoreResult(
        patent_id=pid,
        model="codex",
        plain_english=f"Codex view for {pid}",
        consumer_viable=True,
        bom_estimate="$1-2",
        amazon_gap=True,
        review_signal="gap exists",
        score=9,
    )
    return ScoredPatent(
        patent=patent,
        sonnet=sonnet,
        codex=codex,
        consensus_score=8.5,
        adopted=adopted,
    )


def _embed_chars(embed: dict) -> int:
    total = len(embed.get("title", ""))
    total += len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", ""))
        total += len(field.get("value", ""))
    return total


def test_format_embed_builds_expected_payload() -> None:
    payload = discord.format_embed("2026-W19", [_scored(), _scored("US7654321B2")], 1)

    assert payload["username"] == "Patent Hunter"
    [embed] = payload["embeds"]
    assert embed["title"] == "📋 Patent Hunter — Week 2026-W19"
    assert embed["description"] == "**2 patents** scored 7+ by both Sonnet and Codex."
    assert embed["color"] == 0x198754
    assert len(embed["fields"]) == 1
    [field] = embed["fields"]
    assert field["name"] == "#1 Patent US-1234567-A"
    assert "[`US-1234567-A`](https://patents.google.com/patent/US1234567A)" in field[
        "value"
    ]
    assert "CPC `A47J`" in field["value"]
    assert "Consensus: **8.5** (Sonnet 8 / Codex 9)" in field["value"]
    assert field["inline"] is False
    assert embed["footer"]["text"].startswith("Run: ")
    assert embed["footer"]["text"].endswith("Z")


def test_format_embed_enforces_discord_field_and_total_limits() -> None:
    adopted = [
        _scored(
            f"US-LONG-{idx}",
            title="Very long patent title " * 30,
            plain_english="Detailed plain English explanation. " * 200,
        )
        for idx in range(30)
    ]

    payload = discord.format_embed("2026-W19", adopted, top_n=30)

    [embed] = payload["embeds"]
    assert 1 <= len(embed["fields"]) <= 25
    assert _embed_chars(embed) <= 6000
    for field in embed["fields"]:
        assert len(field["name"]) <= 256
        assert len(field["value"]) <= 1024
    assert embed["fields"][0]["name"].endswith("…")
    assert "…_" in embed["fields"][0]["value"]


@pytest.mark.asyncio
async def test_send_top_patents_posts_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, json: dict):
            calls.append((url, json))
            return httpx.Response(204)

    monkeypatch.setattr(discord.httpx, "AsyncClient", FakeClient)

    ok = await discord.send_top_patents(
        "https://discord.com/api/webhooks/1/token",
        "2026-W19",
        [_scored()],
        timeout_seconds=1.5,
    )

    assert ok is True
    assert len(calls) == 1
    assert calls[0][0] == "https://discord.com/api/webhooks/1/token"
    assert calls[0][1]["username"] == "Patent Hunter"


@pytest.mark.asyncio
async def test_send_top_patents_returns_false_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, json: dict):
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(discord.httpx, "AsyncClient", FakeClient)

    ok = await discord.send_top_patents(
        "https://discord.com/api/webhooks/1/token",
        "2026-W19",
        [_scored()],
    )

    assert ok is False
    assert attempts == 2


def test_runner_emits_notification_failed_without_failing_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_send_top_patents(*args, **kwargs) -> bool:
        return False

    async def fake_sonnet_runner(argv, timeout):
        return json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": json.dumps(
                    [
                        {
                            "patent_id": "A",
                            "plain_english": "Plain for A",
                            "consumer_viable": True,
                            "bom_estimate": "$1-2",
                            "amazon_gap": True,
                            "review_signal": "noise",
                            "score": 8,
                        }
                    ]
                ),
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

    async def fake_codex_runner(argv, timeout):
        return json.dumps(
            [
                {
                    "patent_id": "A",
                    "plain_english": "Codex view of A",
                    "consumer_viable": True,
                    "bom_estimate": "$1-2",
                    "amazon_gap": False,
                    "review_signal": "x",
                    "score": 8,
                }
            ]
        )

    monkeypatch.setattr("patent_hunter.runner.send_top_patents", fake_send_top_patents)

    paths = run(
        RunConfig(
            week=IsoWeek(2026, 19),
            out_dir=tmp_path,
            fetched_patents=[make_patent("A")],
            sonnet_client=fake_sonnet_runner,
            codex_runner=fake_codex_runner,
            discord_webhook_url="https://discord.com/api/webhooks/1/token",
        )
    )

    events = [json.loads(line) for line in paths["events"].read_text().splitlines()]
    assert "notification_failed" in {row["event"] for row in events}
    failed = next(row for row in events if row["event"] == "notification_failed")
    assert failed["level"] == "warn"
    assert failed["week"] == "2026-W19"
    assert paths["report"].exists()

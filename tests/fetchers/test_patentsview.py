"""Fetcher unit tests. The PatentsView HTTP call is fully mocked."""

from __future__ import annotations

from typing import Any, Dict

import httpx

from patent_hunter.fetchers.patentsview import (
    FetchConfig,
    _build_query,
    _flatten_and_cap,
    _flatten_patent,
    _vintage_window,
    fetch_patents,
)
from patent_hunter.week import IsoWeek


def _record(patent_id="123", cpc="A47J 27/00", title="Self-stirring pot") -> Dict[str, Any]:
    return {
        "patent_id": patent_id,
        "patent_title": title,
        "patent_abstract": "An apparatus that stirs itself.",
        "patent_date": "2014-05-06",
        "application": [{"filing_date": "2011-02-01"}],
        "assignees": [{"assignee_organization": "Acme Cookware LLC"}],
        "cpc_current": [{"cpc_subclass_id": cpc}],
        "claim": [
            {"claim_sequence": 0, "claim_text": "1. A pot that stirs."},
            {"claim_sequence": 1, "claim_text": "2. ..."},
        ],
    }


def test_flatten_patent_maps_category():
    p = _flatten_patent(_record())
    assert p is not None
    assert p.patent_id == "123"
    assert p.category == "kitchen"
    assert p.cpc_code == "A47J 27/00"
    assert p.claim_count == 2
    assert p.first_claim and p.first_claim.startswith("1.")
    assert p.assignee_name == "Acme Cookware LLC"
    assert p.google_patents_url.endswith("US123")


def test_flatten_patent_drops_out_of_scope_cpc():
    raw = _record(cpc="H04L 12/00")  # network protocol, out of scope
    assert _flatten_patent(raw) is None


def test_flatten_patent_handles_missing_optional_fields():
    raw = {
        "patent_id": "999",
        "patent_title": "Tiny",
        "patent_date": "2014-01-01",
        "cpc_current": [{"cpc_subclass_id": "A01K"}],
    }
    p = _flatten_patent(raw)
    assert p is not None
    assert p.category == "pet_products"
    assert p.assignee_name is None
    assert p.claim_count == 0
    assert p.first_claim is None


def test_flatten_and_cap_enforces_per_category_limit():
    payload = {
        "patents": [
            _record(patent_id=f"K{i}", cpc="A47J") for i in range(5)
        ] + [
            _record(patent_id=f"P{i}", cpc="A01K") for i in range(3)
        ]
    }
    out = _flatten_and_cap(payload, max_per_category=2)
    by_cat = {}
    for p in out:
        by_cat.setdefault(p.category, []).append(p.patent_id)
    assert len(by_cat["kitchen"]) == 2
    assert len(by_cat["pet_products"]) == 2


def test_vintage_window_shifts_back():
    week = IsoWeek(2026, 19)
    start, end = _vintage_window(week, vintage_years=12)
    assert start.year == 2014
    assert end.year == 2014
    assert (end - start).days == 6


def test_build_query_contains_filters_and_cpc_or():
    body = _build_query(
        start=__import__("datetime").date(2014, 1, 6),
        end=__import__("datetime").date(2014, 1, 12),
        cpc_prefixes=["A47J", "A01K"],
    )
    inner = body["q"]["_and"]
    # date range, utility, and an _or block for CPCs
    has_gte = any("_gte" in c for c in inner)
    has_lte = any("_lte" in c for c in inner)
    has_utility = any(c.get("patent_type") == "utility" for c in inner)
    or_block = next(c for c in inner if "_or" in c)
    assert has_gte and has_lte and has_utility
    assert len(or_block["_or"]) == 2


def test_fetch_patents_uses_mocked_client():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(
            200,
            json={
                "patents": [
                    _record(patent_id="A1", cpc="A47J"),
                    _record(patent_id="A2", cpc="A47J"),
                    _record(patent_id="P1", cpc="A01K"),
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    week = IsoWeek(2026, 19)
    out = fetch_patents(week, FetchConfig(max_per_category=10), client=client)
    assert {p.patent_id for p in out} == {"A1", "A2", "P1"}
    assert "search.patentsview.org" in captured["url"]

"""Shared test helpers."""

from __future__ import annotations

from patent_hunter.models import Patent


def make_patent(
    pid: str = "A",
    title: str | None = None,
    category: str = "kitchen",
    cpc_code: str = "A47J",
) -> Patent:
    """Build a minimal Patent used by multiple test modules."""

    return Patent(
        patent_id=pid,
        title=title or f"Patent {pid}",
        abstract="abstract",
        grant_date="2014-05-06",
        filing_date="2011-01-01",
        assignee_name="Acme",
        cpc_code=cpc_code,
        category=category,
        claim_count=4,
        first_claim="1. ...",
        google_patents_url=f"https://patents.google.com/patent/US{pid}",
    )

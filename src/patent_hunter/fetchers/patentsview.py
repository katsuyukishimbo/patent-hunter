"""Deterministic fetch + filter stage.

Data source decision (P1)
-------------------------
We use PatentsView's public REST API
(https://search.patentsview.org/api/v1/patent/) over the alternatives:

  * USPTO Bulk Data    -- XML dumps; great recall, but requires running an
                          XML parser over multi-GB files just to extract
                          one week of grants. Too heavy for a P1 CLI that
                          must finish a cycle in < 2h.
  * Google Patents BQ  -- powerful, but pulls in a GCP billing account
                          and a BigQuery client, expanding the dependency
                          surface for a single-week query. Reserved for P2.
  * PatentsView REST   -- free, no key required (the v1 endpoint is open
                          for low-volume use), returns JSON, supports CPC
                          and grant-date filters in one call. Lowest
                          implementation cost. Chosen.

"Expired patent" approximation
------------------------------
A US utility patent legally expires when (a) the 20-year term lapses, or
(b) a maintenance fee is missed at the 3.5 / 7.5 / 11.5 year mark.
PatentsView does not expose maintenance-fee events on the public v1
endpoint, so a faithful "lapsed last week" query is impossible in P1.

Practical P1 approximation: we fetch patents *granted* during the target
ISO week ~12 years ago (default 12y; tunable). Patents in that vintage
were granted long enough ago that the 11.5-year fee window has just
elapsed; if the assignee is a small entity and abandoned it, the patent
is now free to copy. This is a recall-favoring approximation that the
LLM scorer further filters by commercial viability.

Limitations (documented for honesty):
  * Some of the 12y-old grants are still in force (maintenance fees paid).
    The LLM still scores them; downstream RFQ would re-verify on Google
    Patents before any cash is spent. P1 does NOT pretend to give legal
    certainty.
  * We cannot fetch the full claim set cheaply, so we score on
    title + abstract + cpc + assignee. Gipp's pipeline uses full text;
    P1 trades recall for cost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import httpx

from .categories import CATEGORY_CPC_PREFIXES, all_prefixes, category_of
from ..models import Patent
from ..week import IsoWeek, format_date

logger = logging.getLogger(__name__)

PATENTSVIEW_URL = "https://search.patentsview.org/api/v1/patent/"
DEFAULT_VINTAGE_YEARS = 12  # see module docstring
DEFAULT_PAGE_SIZE = 100


@dataclass
class FetchConfig:
    vintage_years: int = DEFAULT_VINTAGE_YEARS
    max_per_category: int = 25
    request_timeout: float = 30.0
    api_key: Optional[str] = None  # PatentsView added optional keys; we don't require one.


def _vintage_window(week: IsoWeek, vintage_years: int) -> tuple[date, date]:
    """Return (from_date, to_date) shifted backwards by `vintage_years` years.

    We shift the calendar week as a whole, not day-by-day, so the result
    is still a full Monday-Sunday window in the historical year.
    """
    target_year = week.year - vintage_years
    # date.fromisocalendar will raise if the historical year doesn't have
    # this many ISO weeks (rare). Fall back to week 1 of that year.
    try:
        start = date.fromisocalendar(target_year, week.week, 1)
        end = date.fromisocalendar(target_year, week.week, 7)
    except ValueError:
        start = date.fromisocalendar(target_year, 1, 1)
        end = date.fromisocalendar(target_year, 1, 7)
    return start, end


def _build_query(
    start: date,
    end: date,
    cpc_prefixes: Iterable[str],
) -> Dict[str, Any]:
    """Build the PatentsView POST body.

    PatentsView v1 query DSL: `_and`, `_or`, `_gte`, `_lte`, `_begins`.
    """
    return {
        "q": {
            "_and": [
                {"_gte": {"patent_date": format_date(start)}},
                {"_lte": {"patent_date": format_date(end)}},
                {"patent_type": "utility"},
                {
                    "_or": [
                        {"_begins": {"cpc_current.cpc_subclass_id": prefix}}
                        for prefix in cpc_prefixes
                    ]
                },
            ]
        },
        "f": [
            "patent_id",
            "patent_title",
            "patent_abstract",
            "patent_date",
            "application.filing_date",
            "assignees.assignee_organization",
            "cpc_current.cpc_subclass_id",
            "claim.claim_text",
            "claim.claim_sequence",
        ],
        "s": [{"patent_date": "asc"}],
        "o": {"size": DEFAULT_PAGE_SIZE},
    }


def _flatten_patent(raw: Dict[str, Any]) -> Optional[Patent]:
    """Convert one PatentsView record into our Patent dataclass.

    Returns None if we cannot find a CPC code in any of our target prefixes
    (PatentsView's `_begins` can over-match if a record carries multiple
    CPCs and one of them is in scope while the "primary" one isn't).
    """
    patent_id = raw.get("patent_id") or ""
    if not patent_id:
        return None

    # CPC: PatentsView returns a list. Pick the first one that maps to one of
    # our target categories.
    cpc_list = raw.get("cpc_current") or raw.get("cpcs") or []
    chosen_cpc: Optional[str] = None
    chosen_category: Optional[str] = None
    for entry in cpc_list:
        code = (entry or {}).get("cpc_subclass_id") or (entry or {}).get("subclass_id")
        if not code:
            continue
        cat = category_of(code)
        if cat:
            chosen_cpc = code
            chosen_category = cat
            break
    if not chosen_cpc or not chosen_category:
        return None

    # Claims: pick claim 1 if available.
    claims = raw.get("claim") or raw.get("claims") or []
    first_claim_text = None
    if claims:
        # Prefer claim_sequence == 0 or 1, else first.
        sorted_claims = sorted(
            claims, key=lambda c: (c.get("claim_sequence") or 0)
        )
        first_claim_text = (sorted_claims[0] or {}).get("claim_text")

    assignee_name = None
    assignees = raw.get("assignees") or []
    if assignees:
        assignee_name = (assignees[0] or {}).get("assignee_organization")

    filing_date = None
    app = raw.get("application") or []
    if isinstance(app, list) and app:
        filing_date = (app[0] or {}).get("filing_date")
    elif isinstance(app, dict):
        filing_date = app.get("filing_date")

    return Patent(
        patent_id=patent_id,
        title=raw.get("patent_title") or "",
        abstract=raw.get("patent_abstract") or "",
        grant_date=raw.get("patent_date") or "",
        filing_date=filing_date,
        assignee_name=assignee_name,
        cpc_code=chosen_cpc,
        category=chosen_category,
        claim_count=len(claims),
        first_claim=first_claim_text,
        google_patents_url=f"https://patents.google.com/patent/US{patent_id}",
    )


def fetch_patents(
    week: IsoWeek,
    config: Optional[FetchConfig] = None,
    client: Optional[httpx.Client] = None,
) -> List[Patent]:
    """Fetch + filter patents for the given ISO week.

    Args:
      week: target week (we shift back `vintage_years` years inside).
      config: fetch tuning. Defaults are safe.
      client: an open httpx.Client. If None, a temporary one is created
        and closed inside.

    Returns:
      A list of Patent records, capped at config.max_per_category per
      category (so a 4-cat run never exceeds 4 * max_per_category items).
    """
    cfg = config or FetchConfig()
    owns_client = client is None
    if owns_client:
        client = httpx.Client(headers={"User-Agent": "patent-hunter/0.1"})
    try:
        start, end = _vintage_window(week, cfg.vintage_years)
        logger.info(
            "Fetching PatentsView for grant window %s..%s (vintage_years=%d)",
            format_date(start),
            format_date(end),
            cfg.vintage_years,
        )
        body = _build_query(start, end, all_prefixes())
        headers: Dict[str, str] = {}
        if cfg.api_key:
            headers["X-Api-Key"] = cfg.api_key
        resp = client.post(
            PATENTSVIEW_URL, json=body, headers=headers, timeout=cfg.request_timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        return _flatten_and_cap(payload, cfg.max_per_category)
    finally:
        if owns_client and client is not None:
            client.close()


def _flatten_and_cap(payload: Dict[str, Any], max_per_category: int) -> List[Patent]:
    """Walk the PatentsView response, flatten, and cap per category."""
    raw_list = payload.get("patents") or payload.get("data") or []
    per_cat: Dict[str, int] = {k: 0 for k in CATEGORY_CPC_PREFIXES}
    out: List[Patent] = []
    for raw in raw_list:
        patent = _flatten_patent(raw)
        if patent is None:
            continue
        if per_cat[patent.category] >= max_per_category:
            continue
        per_cat[patent.category] += 1
        out.append(patent)
    logger.info("Kept %d patents after cap (per-category=%s)", len(out), per_cat)
    return out

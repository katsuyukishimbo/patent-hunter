"""Google Patents BigQuery fetcher.

PatentsView's old REST endpoint was decommissioned in 2025 and USPTO's
successor Open Data Portal requires an ID.me-authenticated path that is not
usable for this project. Google Patents Public Datasets mirrors USPTO
publication data in BigQuery with enough freshness for the weekly hunter run.

Design notes:
  * One SQL query covers all CPC categories. It is cheaper and lower latency
    than issuing one query per category, and the downstream LLM scorer is the
    precision filter. The CLI knob is still named ``max_per_category`` for the
    runner contract, but BigQuery receives a single total LIMIT equal to
    category_count * max_per_category.
  * The BigQuery Client is short-lived by default. One hunter cycle performs
    one query, so keeping a long-lived client adds lifecycle code without a
    measurable benefit.
  * BigQuery and ADC failures are wrapped in ``BigQueryPatentFetchError`` so
    callers see a fetch-stage error while retaining the original exception as
    ``__cause__`` for debugging.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, List, Optional

from .categories import CATEGORY_CPC_PREFIXES, all_prefixes, category_of
from ..models import Patent
from ..observability import emit
from ..week import IsoWeek

logger = logging.getLogger(__name__)

GOOGLE_PATENTS_TABLE = "`patents-public-data.patents.publications`"
DEFAULT_VINTAGE_YEARS = 12
DEFAULT_QUERY_TIMEOUT = 60.0
MAX_FETCH_ATTEMPTS = 3
FETCH_RETRY_BASE_DELAY_SECONDS = 1.0
FETCH_RETRY_FACTOR = 2.0


class BigQueryPatentFetchError(RuntimeError):
    """Raised when BigQuery cannot fetch patent candidates."""


class BigQueryRetryExhaustedError(BigQueryPatentFetchError):
    """Raised after transient BigQuery failures exceed the retry budget."""


@dataclass
class FetchConfig:
    vintage_years: int = DEFAULT_VINTAGE_YEARS
    max_per_category: int = 25
    request_timeout: float = DEFAULT_QUERY_TIMEOUT


def _load_google_modules() -> tuple[Any, Any, Any]:
    """Import Google modules lazily so dryrun/test paths do not require ADC."""
    try:
        from google.api_core import exceptions as api_exceptions
        from google.auth import exceptions as auth_exceptions
        from google.cloud import bigquery
    except ImportError as exc:
        raise BigQueryPatentFetchError(
            "google-cloud-bigquery>=3.21.0 is required for live patent fetching. "
            'Install with: .venv/bin/pip install -e ".[dev]"'
        ) from exc
    return bigquery, api_exceptions, auth_exceptions


def _vintage_window(week: IsoWeek, vintage_years: int) -> tuple[date, date]:
    """Return (from_date, to_date) shifted backwards by `vintage_years` years.

    We keep the historical ISO week intact, matching the legacy fetcher's
    recall-favoring "likely lapsed" approximation.
    """
    target_year = week.year - vintage_years
    try:
        start = date.fromisocalendar(target_year, week.week, 1)
        end = date.fromisocalendar(target_year, week.week, 7)
    except ValueError:
        start = date.fromisocalendar(target_year, 1, 1)
        end = date.fromisocalendar(target_year, 1, 7)
    return start, end


def _date_to_int(value: date) -> int:
    """Return BigQuery's YYYYMMDD integer representation."""
    return value.year * 10000 + value.month * 100 + value.day


def _limit_total(max_per_category: int) -> int:
    """Translate the old per-category cost guard into one BigQuery LIMIT."""
    return max(0, max_per_category) * len(CATEGORY_CPC_PREFIXES)


def _build_cpc_clause(cpc_prefixes: Iterable[str]) -> str:
    clauses = [
        f"STARTS_WITH(c.code, @cpc_prefix_{idx})"
        for idx, _prefix in enumerate(cpc_prefixes)
    ]
    if not clauses:
        return "FALSE"
    return "\n       OR ".join(clauses)


def _build_query(cpc_prefixes: Iterable[str]) -> str:
    """Build the Google Patents SQL using CPC prefixes from categories.py."""
    prefixes = list(cpc_prefixes)
    cpc_clause = _build_cpc_clause(prefixes)
    return f"""
SELECT
  p.publication_number AS patent_id,
  p.title_localized[OFFSET(0)].text AS title,
  p.abstract_localized[OFFSET(0)].text AS abstract,
  CAST(p.grant_date AS STRING) AS grant_date,
  CAST(p.filing_date AS STRING) AS filing_date,
  p.assignee_harmonized[OFFSET(0)].name AS assignee_name,
  p.cpc[OFFSET(0)].code AS cpc_code,
  ARRAY_LENGTH(p.claims_localized) AS claim_count,
  SUBSTR(p.claims_localized[OFFSET(0)].text, 0, 1500) AS first_claim
FROM {GOOGLE_PATENTS_TABLE} p
WHERE p.country_code = 'US'
  AND p.kind_code IN ('B1', 'B2')
  AND p.grant_date BETWEEN @start_date_int AND @end_date_int
  AND EXISTS (
    SELECT 1 FROM UNNEST(p.cpc) c
    WHERE {cpc_clause}
  )
LIMIT @limit_total
""".strip()


def _query_parameters(
    bigquery: Any,
    *,
    start_date_int: int,
    end_date_int: int,
    cpc_prefixes: Iterable[str],
    limit_total: int,
) -> list[Any]:
    params = [
        bigquery.ScalarQueryParameter("start_date_int", "INT64", start_date_int),
        bigquery.ScalarQueryParameter("end_date_int", "INT64", end_date_int),
        bigquery.ScalarQueryParameter("limit_total", "INT64", limit_total),
    ]
    params.extend(
        bigquery.ScalarQueryParameter(f"cpc_prefix_{idx}", "STRING", prefix)
        for idx, prefix in enumerate(cpc_prefixes)
    )
    return params


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return getattr(row, key, default)


def _google_patents_url(publication_number: str) -> str:
    return f"https://patents.google.com/patent/{publication_number.replace('-', '')}"


def _flatten_row(row: Any) -> Optional[Patent]:
    patent_id = str(_row_value(row, "patent_id", "") or "")
    if not patent_id:
        return None

    cpc_code = str(_row_value(row, "cpc_code", "") or "")
    category = category_of(cpc_code)
    if not category:
        return None

    first_claim_raw = _row_value(row, "first_claim")
    first_claim = None if first_claim_raw is None else str(first_claim_raw)

    filing_date_raw = _row_value(row, "filing_date")
    filing_date = None if filing_date_raw is None else str(filing_date_raw)

    assignee_raw = _row_value(row, "assignee_name")
    assignee_name = None if assignee_raw is None else str(assignee_raw)

    return Patent(
        patent_id=patent_id,
        title=str(_row_value(row, "title", "") or ""),
        abstract=str(_row_value(row, "abstract", "") or ""),
        grant_date=str(_row_value(row, "grant_date", "") or ""),
        filing_date=filing_date,
        assignee_name=assignee_name,
        cpc_code=cpc_code,
        category=category,
        claim_count=int(_row_value(row, "claim_count", 0) or 0),
        first_claim=first_claim,
        google_patents_url=_google_patents_url(patent_id),
    )


def _flatten_rows(rows: Iterable[Any]) -> List[Patent]:
    patents: List[Patent] = []
    for row in rows:
        patent = _flatten_row(row)
        if patent is not None:
            patents.append(patent)
    return patents


def _retryable_google_errors(api_exceptions: Any) -> tuple[type[BaseException], ...]:
    """Return the transient BigQuery exception classes present in this env."""

    names = ("ResourceExhausted", "DeadlineExceeded", "ServiceUnavailable")
    classes: list[type[BaseException]] = []
    for name in names:
        cls = getattr(api_exceptions, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            classes.append(cls)
    return tuple(classes)


def _sleep_between_attempts(delay: float) -> None:
    """Sleep from sync code while still using asyncio.sleep as the primitive.

    ``fetch_patents`` is sync because the BigQuery client is sync. Runner calls
    it through ``asyncio.to_thread``; direct test/helper calls have no running
    event loop. If someone calls it from an active loop, fall back to
    ``time.sleep`` instead of crashing with ``asyncio.run`` nesting.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(asyncio.sleep(delay))
    else:
        time.sleep(delay)


def fetch_patents(
    week: IsoWeek,
    config: Optional[FetchConfig] = None,
    client: Optional[Any] = None,
) -> List[Patent]:
    """Fetch patent candidates for one ISO week from Google Patents BigQuery."""
    started = time.perf_counter()
    cfg = config or FetchConfig()
    bigquery, api_exceptions, auth_exceptions = _load_google_modules()
    retryable_errors = _retryable_google_errors(api_exceptions)

    start, end = _vintage_window(week, cfg.vintage_years)
    start_int = _date_to_int(start)
    end_int = _date_to_int(end)
    prefixes = all_prefixes()
    limit_total = _limit_total(cfg.max_per_category)
    sql = _build_query(prefixes)
    job_config = bigquery.QueryJobConfig(
        query_parameters=_query_parameters(
            bigquery,
            start_date_int=start_int,
            end_date_int=end_int,
            cpc_prefixes=prefixes,
            limit_total=limit_total,
        )
    )

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
    owns_client = client is None
    try:
        if client is None:
            client = bigquery.Client(project=project)
        emit(
            "fetch_started",
            week=week.label,
            vintage_years=cfg.vintage_years,
            max_per_category=cfg.max_per_category,
        )
        for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
            try:
                logger.info(
                    "Fetching Google Patents BigQuery for grant window %d..%d "
                    "(vintage_years=%d, limit_total=%d, attempt=%d/%d)",
                    start_int,
                    end_int,
                    cfg.vintage_years,
                    limit_total,
                    attempt,
                    MAX_FETCH_ATTEMPTS,
                )
                query_job = client.query(sql, job_config=job_config)
                rows = query_job.result(timeout=cfg.request_timeout)
                patents = _flatten_rows(rows)
                duration_ms = int((time.perf_counter() - started) * 1000)
                emit(
                    "fetch_done",
                    week=week.label,
                    count=len(patents),
                    duration_ms=duration_ms,
                )
                logger.info("Kept %d patents from BigQuery result rows", len(patents))
                return patents
            except retryable_errors as exc:
                if attempt >= MAX_FETCH_ATTEMPTS:
                    raise BigQueryRetryExhaustedError(
                        "BigQuery patent fetch retry exhausted: "
                        f"{exc.__class__.__name__}: {exc}"
                    ) from exc
                delay = FETCH_RETRY_BASE_DELAY_SECONDS * (
                    FETCH_RETRY_FACTOR ** (attempt - 1)
                )
                emit(
                    "fetch_retry",
                    level="warn",
                    week=week.label,
                    attempt=attempt + 1,
                    reason=f"{exc.__class__.__name__}: {exc}",
                )
                logger.warning(
                    "BigQuery transient failure (%s), retrying attempt %d/%d in %.1fs",
                    exc.__class__.__name__,
                    attempt + 1,
                    MAX_FETCH_ATTEMPTS,
                    delay,
                )
                _sleep_between_attempts(delay)
    except (api_exceptions.GoogleAPIError, auth_exceptions.GoogleAuthError) as exc:
        raise BigQueryPatentFetchError(
            f"BigQuery patent fetch failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    finally:
        if owns_client and client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()


fetch_candidates = fetch_patents

__all__ = [
    "BigQueryPatentFetchError",
    "FetchConfig",
    "fetch_candidates",
    "fetch_patents",
]

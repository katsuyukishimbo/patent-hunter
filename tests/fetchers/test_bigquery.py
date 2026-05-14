"""BigQuery fetcher tests. The BigQuery Client is fully mocked."""

from __future__ import annotations

import sys
import types
from unittest.mock import Mock, patch

import pytest

from patent_hunter.fetchers import bigquery as fetcher
from patent_hunter.fetchers.categories import CATEGORY_CPC_PREFIXES, all_prefixes
from patent_hunter.week import IsoWeek


class _ScalarQueryParameter:
    def __init__(self, name: str, type_: str, value):
        self.name = name
        self.type_ = type_
        self.value = value


class _QueryJobConfig:
    def __init__(self, query_parameters=None):
        self.query_parameters = list(query_parameters or [])


class _GoogleAPIError(Exception):
    pass


class _TooManyRequests(_GoogleAPIError):
    pass


class _ResourceExhausted(_GoogleAPIError):
    pass


class _DeadlineExceeded(_GoogleAPIError):
    pass


class _ServiceUnavailable(_GoogleAPIError):
    pass


class _GoogleAuthError(Exception):
    pass


class _DefaultCredentialsError(_GoogleAuthError):
    pass


@pytest.fixture(autouse=True)
def fake_google_modules(monkeypatch: pytest.MonkeyPatch):
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    bq = types.ModuleType("google.cloud.bigquery")
    api_core = types.ModuleType("google.api_core")
    api_exceptions = types.ModuleType("google.api_core.exceptions")
    auth = types.ModuleType("google.auth")
    auth_exceptions = types.ModuleType("google.auth.exceptions")

    bq.Client = Mock(name="Client")
    bq.QueryJobConfig = _QueryJobConfig
    bq.ScalarQueryParameter = _ScalarQueryParameter

    api_exceptions.GoogleAPIError = _GoogleAPIError
    api_exceptions.TooManyRequests = _TooManyRequests
    api_exceptions.ResourceExhausted = _ResourceExhausted
    api_exceptions.DeadlineExceeded = _DeadlineExceeded
    api_exceptions.ServiceUnavailable = _ServiceUnavailable
    auth_exceptions.GoogleAuthError = _GoogleAuthError
    auth_exceptions.DefaultCredentialsError = _DefaultCredentialsError

    cloud.bigquery = bq
    api_core.exceptions = api_exceptions
    auth.exceptions = auth_exceptions

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", api_exceptions)
    monkeypatch.setitem(sys.modules, "google.auth", auth)
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", auth_exceptions)

    return types.SimpleNamespace(
        bigquery=bq,
        api_exceptions=api_exceptions,
        auth_exceptions=auth_exceptions,
    )


def _row(patent_id="US-1234567-B2", cpc="A47J 27/00"):
    return {
        "patent_id": patent_id,
        "title": "Self-stirring pot",
        "abstract": "An apparatus that stirs itself.",
        "grant_date": "20140506",
        "filing_date": "20110201",
        "assignee_name": "Acme Cookware LLC",
        "cpc_code": cpc,
        "claim_count": 2,
        "first_claim": "1. A pot that stirs.",
    }


def _client_for(rows):
    query_job = Mock()
    query_job.result.return_value = rows
    client = Mock()
    client.query.return_value = query_job
    return client


def _query_call(client):
    sql = client.query.call_args.args[0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {param.name: param.value for param in job_config.query_parameters}
    return sql, params


def test_fetch_patents_maps_rows_and_empty_results(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    client = _client_for([_row()])

    with patch("google.cloud.bigquery.Client", return_value=client) as client_cls:
        out = fetcher.fetch_patents(IsoWeek(2026, 19), fetcher.FetchConfig())

    assert len(out) == 1
    assert out[0].patent_id == "US-1234567-B2"
    assert out[0].category == "kitchen"
    assert out[0].first_claim == "1. A pot that stirs."
    assert out[0].google_patents_url.endswith("US1234567B2")
    client_cls.assert_called_once_with(project="demo-project")

    empty_client = _client_for([])
    with patch("google.cloud.bigquery.Client", return_value=empty_client):
        assert fetcher.fetch_patents(IsoWeek(2026, 19), fetcher.FetchConfig()) == []


def test_cpc_filters_are_reflected_in_sql_and_parameters():
    client = _client_for([])

    with patch("google.cloud.bigquery.Client", return_value=client):
        fetcher.fetch_patents(IsoWeek(2026, 19), fetcher.FetchConfig())

    sql, params = _query_call(client)
    for idx, prefix in enumerate(all_prefixes()):
        assert f"STARTS_WITH(c.code, @cpc_prefix_{idx})" in sql
        assert params[f"cpc_prefix_{idx}"] == prefix


def test_vintage_years_sets_grant_date_between_parameters():
    client = _client_for([])

    with patch("google.cloud.bigquery.Client", return_value=client):
        fetcher.fetch_patents(
            IsoWeek(2026, 19),
            fetcher.FetchConfig(vintage_years=12),
        )

    sql, params = _query_call(client)
    assert "p.grant_date BETWEEN @start_date_int AND @end_date_int" in sql
    assert params["start_date_int"] == 20140505
    assert params["end_date_int"] == 20140511


def test_max_per_category_sets_total_limit_parameter():
    client = _client_for([])

    with patch("google.cloud.bigquery.Client", return_value=client):
        fetcher.fetch_patents(
            IsoWeek(2026, 19),
            fetcher.FetchConfig(max_per_category=7),
        )

    sql, params = _query_call(client)
    assert "LIMIT @limit_total" in sql
    assert params["limit_total"] == 7 * len(CATEGORY_CPC_PREFIXES)


def test_auth_failure_is_wrapped(fake_google_modules):
    with patch(
        "google.cloud.bigquery.Client",
        side_effect=fake_google_modules.auth_exceptions.DefaultCredentialsError(
            "missing ADC"
        ),
    ):
        with pytest.raises(fetcher.BigQueryPatentFetchError) as excinfo:
            fetcher.fetch_patents(IsoWeek(2026, 19), fetcher.FetchConfig())

    assert "DefaultCredentialsError" in str(excinfo.value)
    assert isinstance(
        excinfo.value.__cause__,
        fake_google_modules.auth_exceptions.DefaultCredentialsError,
    )


def test_quota_failure_is_wrapped(fake_google_modules):
    query_job = Mock()
    query_job.result.side_effect = fake_google_modules.api_exceptions.TooManyRequests(
        "quota exceeded"
    )
    client = Mock()
    client.query.return_value = query_job

    with patch("google.cloud.bigquery.Client", return_value=client):
        with pytest.raises(fetcher.BigQueryPatentFetchError) as excinfo:
            fetcher.fetch_patents(IsoWeek(2026, 19), fetcher.FetchConfig())

    assert "TooManyRequests" in str(excinfo.value)
    assert isinstance(
        excinfo.value.__cause__,
        fake_google_modules.api_exceptions.TooManyRequests,
    )


def test_retryable_bigquery_error_retries_then_succeeds(
    fake_google_modules, monkeypatch: pytest.MonkeyPatch
):
    async def no_sleep(delay):
        return None

    monkeypatch.setattr(fetcher.asyncio, "sleep", no_sleep)

    query_job = Mock()
    query_job.result.side_effect = [
        fake_google_modules.api_exceptions.ResourceExhausted("quota busy"),
        fake_google_modules.api_exceptions.ResourceExhausted("quota busy"),
        [_row()],
    ]
    client = Mock()
    client.query.return_value = query_job

    with patch("google.cloud.bigquery.Client", return_value=client):
        out = fetcher.fetch_patents(IsoWeek(2026, 19), fetcher.FetchConfig())

    assert len(out) == 1
    assert client.query.call_count == 3
    assert query_job.result.call_count == 3

"""Real BigQuery integration tests for Google Patents public data."""

from __future__ import annotations

from typing import Any

import pytest


QUERY_TIMEOUT_SECONDS = 30


@pytest.mark.integration
def test_bigquery_project_can_run_small_query(patent_client: Any, bigquery_module: Any):
    job_config = bigquery_module.QueryJobConfig(use_query_cache=True)

    rows = list(
        patent_client.query(
            "SELECT 1 AS ok",
            job_config=job_config,
        ).result(timeout=QUERY_TIMEOUT_SECONDS)
    )

    assert len(rows) == 1
    assert rows[0]["ok"] == 1


@pytest.mark.integration
def test_patents_publications_expected_schema(
    patent_client: Any,
    bigquery_module: Any,
):
    sql = """
SELECT
  publication_number,
  (SELECT t.text FROM UNNEST(title_localized) t WHERE t.text IS NOT NULL LIMIT 1)
    AS title,
  CAST(grant_date AS STRING) AS grant_date,
  ARRAY(
    SELECT c.code
    FROM UNNEST(cpc) c
    WHERE c.code IS NOT NULL
    LIMIT 5
  ) AS cpc
FROM `patents-public-data.patents.publications`
WHERE country_code = 'US'
  AND publication_number IS NOT NULL
  AND grant_date IS NOT NULL
  AND ARRAY_LENGTH(cpc) > 0
  AND EXISTS (
    SELECT 1 FROM UNNEST(title_localized) t WHERE t.text IS NOT NULL
  )
LIMIT 1
""".strip()
    job_config = bigquery_module.QueryJobConfig(use_query_cache=True)

    rows = list(
        patent_client.query(sql, job_config=job_config).result(
            timeout=QUERY_TIMEOUT_SECONDS
        )
    )

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row["publication_number"], str)
    assert row["publication_number"]
    assert isinstance(row["title"], str)
    assert row["title"]
    assert isinstance(row["grant_date"], str)
    assert row["grant_date"].isdigit()
    assert len(row["grant_date"]) == 8
    assert isinstance(row["cpc"], list)
    assert row["cpc"]
    assert all(isinstance(code, str) and code for code in row["cpc"])

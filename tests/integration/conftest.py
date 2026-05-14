"""Integration-test fixtures.

These tests are opt-in because they can hit real external services.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


INTEGRATION_ENV = "PATENT_HUNTER_INTEGRATION"
INTEGRATION_DIR = Path(__file__).parent.resolve()


def pytest_configure(config: pytest.Config) -> None:
    """Keep the marker known even if this file is used outside pyproject."""

    markers = config.getini("markers")
    if not any(marker.startswith("integration:") for marker in markers):
        config.addinivalue_line(
            "markers",
            "integration: tests that hit real external services.",
        )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Treat every test in this package as integration-scoped."""

    marker = pytest.mark.integration
    for item in items:
        item_path = Path(str(item.path)).resolve()
        if item_path.is_relative_to(INTEGRATION_DIR):
            item.add_marker(marker)


@pytest.fixture(autouse=True)
def require_integration_env() -> None:
    if os.environ.get(INTEGRATION_ENV) != "1":
        pytest.skip(f"set {INTEGRATION_ENV}=1 to run integration tests")


@pytest.fixture(scope="session")
def gcp_project() -> str:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        pytest.skip("GOOGLE_CLOUD_PROJECT is required for BigQuery integration tests")
    return project


@pytest.fixture(scope="session")
def bigquery_module() -> Any:
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        pytest.skip(f"google-cloud-bigquery is required: {exc}")
    return bigquery


@pytest.fixture(scope="session")
def patent_client(gcp_project: str, bigquery_module: Any) -> Iterator[Any]:
    client = bigquery_module.Client(project=gcp_project)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

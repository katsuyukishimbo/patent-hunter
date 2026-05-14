"""Fetcher protocol for future patent and marketplace data sources."""

from __future__ import annotations

from typing import Protocol

from patent_hunter.models import Patent
from patent_hunter.week import IsoWeek


class FetcherProtocol(Protocol):
    """Callable fetcher contract used by the runner and graph nodes.

    The runner uses the package's default fetch function directly. P3/P4 can
    add JPO/EPO or Alibaba pollers without forcing concrete inheritance;
    structural typing is enough for this small orchestration boundary.
    """

    def __call__(self, week: IsoWeek, config: object | None = None) -> list[Patent]:
        """Fetch candidates for one ISO week."""

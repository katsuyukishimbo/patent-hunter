"""Model dataclass tests."""

from __future__ import annotations

from patent_hunter.models import RunStats


def test_run_stats_budget_remaining() -> None:
    stats = RunStats(
        week_label="2026-W19",
        started_at="2026-05-14T00:00:00Z",
        sonnet_cost_usd=1.2345,
        codex_cost_usd_estimate=0.3,
        budget_max_usd=2.0,
    )

    assert stats.total_cost_usd == 1.5345
    assert stats.budget_remaining_usd == 0.4655

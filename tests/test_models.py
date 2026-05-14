"""Model dataclass tests."""

from __future__ import annotations

from patent_hunter.models import RunStats, ScoreResult


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


def test_score_result_new_fields_have_safe_defaults() -> None:
    score = ScoreResult(patent_id="A", model="sonnet")

    assert score.short_title_ja == ""
    assert score.summary_ja == ""
    assert score.opportunity_ja == ""
    assert score.next_action_steps_ja == []
    assert score.failure_reasons_ja == []
    assert score.failure_mitigations_ja == []
    assert score.confidence_score is None
    assert score.confidence_bom is None
    assert score.confidence_amazon_gap is None
    assert score.diy_friendly is None
    assert score.diy_print_minutes is None
    assert score.diy_material_cost_jpy is None
    assert score.diy_required_extras == []
    assert score.diy_score is None

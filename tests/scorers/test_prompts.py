"""Prompt contract tests."""

from __future__ import annotations

from patent_hunter.scorers.prompts import SYSTEM_PROMPT


def test_system_prompt_mentions_japanese_and_diy_fields() -> None:
    assert "short_title_ja" in SYSTEM_PROMPT
    assert "summary_ja" in SYSTEM_PROMPT
    assert "opportunity_ja" in SYSTEM_PROMPT
    assert "next_action_steps_ja" in SYSTEM_PROMPT
    assert "diy_friendly" in SYSTEM_PROMPT
    assert "diy_print_minutes" in SYSTEM_PROMPT
    assert "diy_material_cost_jpy" in SYSTEM_PROMPT
    assert "diy_required_extras" in SYSTEM_PROMPT
    assert "diy_score" in SYSTEM_PROMPT
    assert "200x200x200mm" in SYSTEM_PROMPT
    assert "Bambu Lab P1S" in SYSTEM_PROMPT
    assert "Etsy" in SYSTEM_PROMPT
    assert "Alibaba" in SYSTEM_PROMPT
    assert "Amazon FBA" in SYSTEM_PROMPT

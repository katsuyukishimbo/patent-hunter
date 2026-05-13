"""LangGraph state schema."""

from __future__ import annotations

from typing import NotRequired, TypedDict

from patent_hunter.models import Patent, ScoredPatent, ScoreResult


class PatentHunterState(TypedDict, total=False):
    """LangGraph state for one weekly run.

    TypedDict is the smallest useful fit here: LangGraph's StateGraph natively
    treats state as a dict, while the domain objects already exist as
    dataclasses. Pydantic/dataclass state would add validation and conversion
    code without improving the graph, so YAGNI says keep state structural.
    """

    week: str
    fetched_patents: list[Patent]
    sonnet_results: list[ScoreResult]
    codex_results: list[ScoreResult]
    scored_patents: list[ScoredPatent]
    adopted: list[ScoredPatent]
    cost_usd: float
    started_at: NotRequired[str]
    sonnet_input_tokens: NotRequired[int]
    sonnet_output_tokens: NotRequired[int]
    sonnet_cost_usd: NotRequired[float]
    codex_invocations: NotRequired[int]
    codex_cost_usd_estimate: NotRequired[float]
    report_paths: NotRequired[dict[str, str]]

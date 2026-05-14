"""Plain dataclasses used across the pipeline.

We intentionally keep these as light dataclasses rather than full Pydantic
models -- they only travel inside this process and through JSONL.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Patent:
    """A USPTO utility patent candidate.

    Fields are deliberately minimal: title + abstract + first claim is what
    the scorer actually reads. Extra fields are kept only for the report.
    """

    patent_id: str
    title: str
    abstract: str
    grant_date: str  # Date string returned by the active fetcher
    filing_date: Optional[str]
    assignee_name: Optional[str]
    cpc_code: str
    category: str
    claim_count: int
    first_claim: Optional[str] = None
    google_patents_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreResult:
    """Output of a single scorer invocation for a single patent."""

    patent_id: str
    model: str  # "sonnet" or "codex"
    plain_english: str = ""
    consumer_viable: Optional[bool] = None
    bom_estimate: str = ""
    amazon_gap: Optional[bool] = None
    review_signal: str = ""
    score: int = 0
    raw: str = ""  # raw JSON text (for debugging)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoredPatent:
    """A patent + both model scores + agreement flag."""

    patent: Patent
    sonnet: ScoreResult
    codex: ScoreResult
    consensus_score: float
    adopted: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patent": self.patent.to_dict(),
            "sonnet": self.sonnet.to_dict(),
            "codex": self.codex.to_dict(),
            "consensus_score": self.consensus_score,
            "adopted": self.adopted,
        }


@dataclass
class RunStats:
    """Run-level metrics persisted to run.log."""

    week_label: str
    started_at: str
    ended_at: str = ""
    fetched: int = 0
    after_filter: int = 0
    scored: int = 0
    adopted: int = 0
    sonnet_input_tokens: int = 0
    sonnet_output_tokens: int = 0
    sonnet_cost_usd: float = 0.0
    sonnet_errors: int = 0
    codex_invocations: int = 0
    codex_cost_usd_estimate: float = 0.0
    codex_errors: int = 0
    budget_max_usd: float = 10.0
    errors: List[str] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return round(self.sonnet_cost_usd + self.codex_cost_usd_estimate, 4)

    @property
    def budget_remaining_usd(self) -> float:
        return round(self.budget_max_usd - self.total_cost_usd, 4)

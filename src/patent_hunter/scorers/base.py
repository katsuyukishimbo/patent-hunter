"""Scorer protocol for future model providers."""

from __future__ import annotations

from typing import Any, Protocol

from patent_hunter.models import Patent


class ScorerProtocol(Protocol):
    """Async callable scorer contract.

    Current scorers expose module-level ``score_batch`` functions rather than
    classes. A callable Protocol keeps that shape while leaving room for Opus,
    Gemini, or hosted scoring adapters later.
    """

    async def __call__(self, patents: list[Patent], **kwargs: Any) -> Any:
        """Score one batch and return a provider-specific batch result."""

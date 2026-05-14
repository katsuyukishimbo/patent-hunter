"""Notification Protocol placeholder for future interactive workflows.

Phase 2 ships one-way Discord webhooks in ``discord.py``. P3 will add Bot /
Interaction / Approval Gate workflows. This module keeps the structural
boundary for those future notification sinks.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class NotifierProtocol(Protocol):
    """Async notification sink used by future approval workflows."""

    async def notify(
        self, message: str, *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        """Send one notification."""

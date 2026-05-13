"""Notification Protocol placeholder for P3.

P3 will add Discord notifications and the Approval Gate. This module only
defines the structural boundary now so the package layout is stable without
shipping an unused implementation.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class NotifierProtocol(Protocol):
    """Async notification sink used by future approval workflows."""

    async def notify(
        self, message: str, *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        """Send one notification."""

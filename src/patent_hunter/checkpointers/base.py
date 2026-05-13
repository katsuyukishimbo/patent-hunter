"""Checkpoint Protocol placeholder for P4.

P4 will add durable Postgres/SQLite checkpoint backends. Until then the
LangGraph wrapper uses MemorySaver, and this module intentionally contains
only the Protocol needed to keep the future package boundary explicit.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class CheckpointerProtocol(Protocol):
    """Minimal key-value checkpoint contract for future durable stores."""

    def get(self, key: str) -> Mapping[str, Any] | None:
        """Return a checkpoint state by key, if present."""

    def put(self, key: str, state: Mapping[str, Any]) -> None:
        """Persist a checkpoint state by key."""

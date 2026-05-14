"""Notification extension point."""

from .base import NotifierProtocol
from .discord import format_embed, send_top_patents

__all__ = ["NotifierProtocol", "format_embed", "send_top_patents"]

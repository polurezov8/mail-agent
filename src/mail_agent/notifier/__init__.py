"""Notifier seam — abstract Slack delivery behind a swappable interface.

Two adapters today:
  - SlackNotifier — posts to Slack via WebClient
  - NullNotifier  — silent no-op for dry-runs, tests, and unconfigured installs

Use get_notifier() to pick the right adapter from environment.
"""

from __future__ import annotations

import os

from .base import (
    BriefPayload,
    DispatchSummary,
    Notifier,
    SearchPayload,
    StatsPayload,
    TriagePayload,
)
from .null import NullNotifier
from .slack import SlackNotifier


def get_notifier() -> Notifier:
    """Return SlackNotifier if SLACK_BOT_TOKEN is set, else NullNotifier."""
    if os.environ.get("SLACK_BOT_TOKEN"):
        return SlackNotifier()
    return NullNotifier()


__all__ = [
    "BriefPayload",
    "DispatchSummary",
    "Notifier",
    "NullNotifier",
    "SearchPayload",
    "SlackNotifier",
    "StatsPayload",
    "TriagePayload",
    "get_notifier",
]

"""Notifier protocol + typed payloads.

Every delivery use case has a verb method here. Payload dataclasses bundle
the inputs so that call sites don't have to know how the notifier formats
or routes the message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..models import EmailMessage, TriageResult
from ..search import SearchPlan
from ..slack.types import DispatchSummary

if TYPE_CHECKING:
    from ..brief import BriefSummary
    from ..store.stats import MetricsSnapshot

__all__ = [
    "BriefPayload",
    "DispatchSummary",
    "Notifier",
    "SearchPayload",
    "StatsPayload",
    "TriagePayload",
]


@dataclass(frozen=True)
class TriagePayload:
    results: list[TriageResult]
    show_account: bool


@dataclass(frozen=True)
class SearchPayload:
    query: str
    gmail_query: str
    hits: list[EmailMessage]
    plan: SearchPlan


@dataclass(frozen=True)
class StatsPayload:
    metrics: "MetricsSnapshot"
    period_label: str


@dataclass(frozen=True)
class BriefPayload:
    summary: "BriefSummary"
    hours: int


class Notifier(Protocol):
    """Outbound delivery interface.

    Implementations: SlackNotifier (real), NullNotifier (no-op).
    Callers should never branch on "is Slack configured" — get_notifier()
    returns NullNotifier when it isn't, and NullNotifier silently swallows.
    """

    @property
    def enabled(self) -> bool:
        """True if posts will reach a backend, False for NullNotifier.

        Call sites use this only for UX messages ("Slack not configured; skipped"),
        not to skip delivery — calling post_X on a disabled notifier is safe.
        """
        ...

    def post_triage(self, payload: TriagePayload) -> DispatchSummary: ...

    def post_search(self, payload: SearchPayload) -> None: ...

    def post_stats(self, payload: StatsPayload) -> None: ...

    def post_brief(self, payload: BriefPayload) -> None: ...

    def post_test_message(self) -> str | None:
        """Send a "connection OK" message. Returns the channel id on success,
        None when the notifier is disabled."""
        ...

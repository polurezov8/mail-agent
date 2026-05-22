"""No-op notifier. Used when Slack isn't configured, in dry-runs, and in tests."""

from __future__ import annotations

from ..slack.types import DispatchSummary
from .base import BriefPayload, Notifier, SearchPayload, StatsPayload, TriagePayload


class NullNotifier(Notifier):
    """All methods silently succeed. post_triage reports everything as skipped."""

    enabled = False

    def post_triage(self, payload: TriagePayload) -> DispatchSummary:
        return DispatchSummary(
            realtime_posts=0,
            digest_mails=0,
            skipped=len(payload.results),
        )

    def post_search(self, payload: SearchPayload) -> None:
        return None

    def post_stats(self, payload: StatsPayload) -> None:
        return None

    def post_brief(self, payload: BriefPayload) -> None:
        return None

    def post_test_message(self) -> str | None:
        return None

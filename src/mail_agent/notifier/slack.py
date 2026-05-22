"""Slack adapter for Notifier. Absorbs the old slack/dispatcher routing logic."""

from __future__ import annotations

from ..models import Bucket, SurfaceResult
from ..slack.blocks import (
    brief_blocks,
    digest_blocks,
    respond_blocks,
    search_results_blocks,
    split_digest,
    stats_blocks,
    uncertain_ignore_blocks,
)
from ..slack.client import get_channel_id, get_client
from ..slack.types import DispatchSummary
from .base import BriefPayload, Notifier, SearchPayload, StatsPayload, TriagePayload


class SlackNotifier(Notifier):
    """Posts notifier payloads to Slack via WebClient.

    Channel and client are resolved lazily on first use (so constructing a
    notifier in test setup doesn't require env vars).
    """

    enabled = True

    def __init__(self) -> None:
        self._client = None
        self._channel = None

    def _ensure(self) -> tuple[object, str]:
        if self._client is None:
            self._client = get_client()
            self._channel = get_channel_id()
        return self._client, self._channel  # type: ignore[return-value]

    def post_triage(self, payload: TriagePayload) -> DispatchSummary:
        """Bucket results and post:
          - respond → one realtime message per mail
          - notify → batched digest (chunks of ≤24 rows)
          - ignore (low-confidence) → uncertain review digest
          - ignore (high-confidence, auto-marked) → silent (not in surface)
        """
        results = payload.results
        show_account = payload.show_account

        surface = [r for r in results if isinstance(r, SurfaceResult)]
        realtime = [r for r in surface if r.decision.bucket == Bucket.RESPOND]
        digest = [r for r in surface if r.decision.bucket == Bucket.NOTIFY]
        uncertain = [r for r in surface if r.decision.bucket == Bucket.IGNORE]
        skipped = len(results) - len(realtime) - len(digest) - len(uncertain)

        if not realtime and not digest and not uncertain:
            return DispatchSummary(
                realtime_posts=0, digest_mails=0, skipped=skipped,
            )

        client, channel = self._ensure()

        realtime_posts = 0
        for r in realtime:
            client.chat_postMessage(
                channel=channel,
                blocks=respond_blocks(r, show_account=show_account),
                text=f"Mail needs response: {r.email.subject}",
                unfurl_links=False,
                unfurl_media=False,
            )
            realtime_posts += 1

        digest_mails = 0
        for chunk in split_digest(digest):
            client.chat_postMessage(
                channel=channel,
                blocks=digest_blocks(chunk, show_account=show_account),
                text=f"Mail digest · {len(chunk)} item(s)",
                unfurl_links=False,
                unfurl_media=False,
            )
            digest_mails += len(chunk)

        for chunk in split_digest(uncertain):
            client.chat_postMessage(
                channel=channel,
                blocks=uncertain_ignore_blocks(chunk, show_account=show_account),
                text=f"Uncertain auto-marks · {len(chunk)} item(s)",
                unfurl_links=False,
                unfurl_media=False,
            )
            digest_mails += len(chunk)

        return DispatchSummary(
            realtime_posts=realtime_posts,
            digest_mails=digest_mails,
            skipped=skipped,
        )

    def post_search(self, payload: SearchPayload) -> None:
        client, channel = self._ensure()
        client.chat_postMessage(
            channel=channel,
            blocks=search_results_blocks(
                payload.query,
                payload.gmail_query,
                payload.hits,
                payload.plan.reasoning,
            ),
            text=f"Search · {payload.query[:80]}",
            unfurl_links=False,
            unfurl_media=False,
        )

    def post_stats(self, payload: StatsPayload) -> None:
        client, channel = self._ensure()
        client.chat_postMessage(
            channel=channel,
            blocks=stats_blocks(payload.metrics),
            text=f"Mail Stats · {payload.period_label}",
            unfurl_links=False,
            unfurl_media=False,
        )

    def post_brief(self, payload: BriefPayload) -> None:
        client, channel = self._ensure()
        client.chat_postMessage(
            channel=channel,
            blocks=brief_blocks(payload.summary),
            text=f"Mail Brief · last {payload.hours}h",
            unfurl_links=False,
            unfurl_media=False,
        )

    def post_test_message(self) -> str | None:
        client, channel = self._ensure()
        client.chat_postMessage(
            channel=channel,
            text="✅ mail-agent connected to Slack.",
        )
        return channel

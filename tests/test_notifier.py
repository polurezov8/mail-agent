from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mail_agent.models import (
    Bucket,
    MessageId,
    SurfaceResult,
)
from mail_agent.notifier import (
    BriefPayload,
    NullNotifier,
    SearchPayload,
    SlackNotifier,
    StatsPayload,
    TriagePayload,
    get_notifier,
)


# ---------------------------------------------------------------- #
# get_notifier() selects the right adapter from env.
# ---------------------------------------------------------------- #


def test_get_notifier_returns_null_when_no_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    n = get_notifier()
    assert isinstance(n, NullNotifier)
    assert n.enabled is False


def test_get_notifier_returns_slack_when_token_set(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    n = get_notifier()
    assert isinstance(n, SlackNotifier)
    assert n.enabled is True


# ---------------------------------------------------------------- #
# NullNotifier silently swallows all posts.
# ---------------------------------------------------------------- #


def test_null_notifier_post_triage_returns_skipped_summary(sample_email, decision_factory):
    n = NullNotifier()
    payload = TriagePayload(
        results=[
            SurfaceResult(email=sample_email(id=MessageId("a")), decision=decision_factory()),
            SurfaceResult(email=sample_email(id=MessageId("b")), decision=decision_factory()),
        ],
        show_account=False,
    )
    summary = n.post_triage(payload)
    assert summary["realtime_posts"] == 0
    assert summary["digest_mails"] == 0
    assert summary["skipped"] == 2


def test_null_notifier_post_stats_brief_search_are_noop(sample_email):
    n = NullNotifier()
    # Should not raise even when payload fields would be invalid for a real backend.
    assert n.post_stats(StatsPayload(metrics=None, period_label="day")) is None  # type: ignore[arg-type]
    assert n.post_brief(BriefPayload(summary=None, hours=24)) is None  # type: ignore[arg-type]
    assert n.post_test_message() is None


# ---------------------------------------------------------------- #
# SlackNotifier routing — replace the WebClient with a recorder.
# Proves bucket-to-message-shape mapping without hitting Slack.
# ---------------------------------------------------------------- #


@dataclass
class _RecordingClient:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def chat_postMessage(self, **kwargs):  # noqa: N802 — matches slack_sdk signature
        self.calls.append(kwargs)
        return {"ok": True, "ts": f"ts-{len(self.calls)}"}


@pytest.fixture
def slack_notifier_with_recorder():
    """SlackNotifier wired to a fake client + fixed channel."""
    notifier = SlackNotifier()
    recorder = _RecordingClient()
    notifier._client = recorder  # noqa: SLF001 — test seam
    notifier._channel = "C-TEST"  # noqa: SLF001
    return notifier, recorder


def test_slack_notifier_routes_respond_to_realtime(
    slack_notifier_with_recorder, sample_email, decision_factory
):
    notifier, recorder = slack_notifier_with_recorder
    payload = TriagePayload(
        results=[
            SurfaceResult(
                email=sample_email(id=MessageId("r1")),
                decision=decision_factory(bucket=Bucket.RESPOND),
            ),
        ],
        show_account=False,
    )
    summary = notifier.post_triage(payload)
    assert summary["realtime_posts"] == 1
    assert summary["digest_mails"] == 0
    assert len(recorder.calls) == 1
    assert "needs response" in recorder.calls[0]["text"].lower()


def test_slack_notifier_routes_notify_to_digest(
    slack_notifier_with_recorder, sample_email, decision_factory
):
    notifier, recorder = slack_notifier_with_recorder
    payload = TriagePayload(
        results=[
            SurfaceResult(
                email=sample_email(id=MessageId(f"n{i}")),
                decision=decision_factory(bucket=Bucket.NOTIFY),
            )
            for i in range(3)
        ],
        show_account=False,
    )
    summary = notifier.post_triage(payload)
    assert summary["digest_mails"] == 3
    assert summary["realtime_posts"] == 0
    # All three fit in one chunk (≤24 cap).
    assert len(recorder.calls) == 1


def test_slack_notifier_routes_ignore_to_uncertain_digest(
    slack_notifier_with_recorder, sample_email, decision_factory
):
    notifier, recorder = slack_notifier_with_recorder
    payload = TriagePayload(
        results=[
            SurfaceResult(
                email=sample_email(id=MessageId("u1")),
                decision=decision_factory(bucket=Bucket.IGNORE),
            ),
        ],
        show_account=False,
    )
    summary = notifier.post_triage(payload)
    assert summary["digest_mails"] == 1
    assert "uncertain" in recorder.calls[0]["text"].lower()


def test_slack_notifier_empty_results_no_calls(slack_notifier_with_recorder):
    notifier, recorder = slack_notifier_with_recorder
    summary = notifier.post_triage(TriagePayload(results=[], show_account=False))
    assert recorder.calls == []
    assert summary["realtime_posts"] == 0
    assert summary["digest_mails"] == 0


def test_slack_notifier_post_search_uses_payload(
    slack_notifier_with_recorder, sample_email
):
    notifier, recorder = slack_notifier_with_recorder
    from mail_agent.search import SearchPlan

    plan = SearchPlan(gmail_query="from:foo", reasoning="exact sender match", suggested_limit=10)
    notifier.post_search(
        SearchPayload(
            query="from foo",
            gmail_query="from:foo",
            hits=[sample_email(id=MessageId("h1"))],
            plan=plan,
        ),
    )
    assert len(recorder.calls) == 1
    assert "Search · from foo" in recorder.calls[0]["text"]

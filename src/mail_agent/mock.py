from __future__ import annotations

from datetime import datetime, timezone

from .models import AccountName, EmailMessage, MessageId, ThreadId

_ACCT = AccountName("mock")


MOCK_INBOX: list[EmailMessage] = [
    EmailMessage(
        id=MessageId("m1"),
        thread_id=ThreadId("t1"),
        account=_ACCT,
        from_email="notifications@github.com",
        from_name="GitHub",
        to=["you@example.com"],
        subject="[repo/foo] PR #123 was merged",
        snippet="Your PR was merged into main.",
        headers={"List-Unsubscribe": "<mailto:unsub@github.com>"},
        received_at=datetime.now(timezone.utc),
    ),
    EmailMessage(
        id=MessageId("m2"),
        thread_id=ThreadId("t2"),
        account=_ACCT,
        from_email="ceo@yourco.test",
        from_name="CEO",
        to=["you@example.com"],
        subject="Quick question about Q3 roadmap",
        snippet="Can you confirm the dates we discussed yesterday?",
        headers={},
        received_at=datetime.now(timezone.utc),
    ),
    EmailMessage(
        id=MessageId("m3"),
        thread_id=ThreadId("t3"),
        account=_ACCT,
        from_email="invite@calendar.google.com",
        from_name="Google Calendar",
        to=["you@example.com"],
        subject="Invitation: Design review @ Thu Jan 14",
        snippet="You have been invited to: Design review",
        headers={"Content-Type": "text/calendar; method=REQUEST"},
        received_at=datetime.now(timezone.utc),
    ),
]

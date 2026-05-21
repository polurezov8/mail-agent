"""Multi-account isolation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from mail_agent.models import (
    AccountName,
    AutoMarkResult,
    Bucket,
    EmailMessage,
    MessageId,
    SurfaceResult,
    ThreadId,
    TriageDecision,
)
from mail_agent.store.sqlite import (
    init_db,
    log_correction,
    mark_processed,
    processed_ids,
    sender_override,
)


def _email(account: str, msg_id: str, from_email: str = "test@example.com") -> EmailMessage:
    return EmailMessage(
        id=MessageId(msg_id),
        thread_id=ThreadId(f"t-{msg_id}"),
        account=AccountName(account),
        from_email=from_email,
        from_name=None,
        to=["me@example.com"],
        subject="Subject",
        snippet="Snippet",
        headers={},
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _decision(bucket: Bucket = Bucket.IGNORE) -> TriageDecision:
    return TriageDecision(
        bucket=bucket,
        rule_name=None,
        reasoning="test",
        confidence=1.0,
        source="header_rule",
        model=None,
    )


def _surface(email: EmailMessage, bucket: Bucket = Bucket.IGNORE) -> SurfaceResult:
    return SurfaceResult(email=email, decision=_decision(bucket))


def _auto(email: EmailMessage) -> AutoMarkResult:
    return AutoMarkResult(email=email, decision=_decision(Bucket.IGNORE))


# ── processed_ids isolation ──────────────────────────────────────────────────


def test_processed_ids_isolated_by_account(isolated_db):
    """Same message_id in two accounts are independent rows."""
    init_db()
    personal = _email("personal", "msg-1")
    work = _email("work", "msg-1")

    mark_processed([_surface(personal), _surface(work)])

    personal_found = processed_ids(AccountName("personal"), [MessageId("msg-1")])
    work_found = processed_ids(AccountName("work"), [MessageId("msg-1")])
    other_found = processed_ids(AccountName("archive"), [MessageId("msg-1")])

    assert MessageId("msg-1") in personal_found
    assert MessageId("msg-1") in work_found
    assert not other_found  # account not in DB → empty


def test_processed_ids_cross_account_no_leak(isolated_db):
    """processed_ids for account A does not return messages stored under B."""
    init_db()
    work_msg = _email("work", "work-only")
    mark_processed([_surface(work_msg)])

    # personal account should not see work's message
    found = processed_ids(AccountName("personal"), [MessageId("work-only")])
    assert not found


# ── corrections / sender_override isolation ─────────────────────────────────


def test_sender_override_applies_cross_account(isolated_db):
    """Sender overrides are keyed by email address, not account — intentional."""
    init_db()
    # Add correction for "work" account but same from_email
    log_correction(
        account="work",
        message_id="m-work-1",
        original_bucket="notify",
        original_rule=None,
        corrected_bucket="ignore",
        note=None,
        from_email="spam@vendor.com",
        subject="Ad",
        apply_to_sender=True,
    )
    # sender_override is cross-account by design (you don't want the same
    # sender treated differently across inboxes)
    assert sender_override("spam@vendor.com") == "ignore"


def test_sender_override_not_applied_without_apply_to_sender(isolated_db):
    init_db()
    log_correction(
        account="personal",
        message_id="m-p-1",
        original_bucket="notify",
        original_rule=None,
        corrected_bucket="respond",
        note=None,
        from_email="boss@corp.com",
        subject="Meeting",
        apply_to_sender=False,
    )
    assert sender_override("boss@corp.com") is None


# ── Slack badge rendering ────────────────────────────────────────────────────


def test_respond_blocks_no_badge_when_single_account():
    from mail_agent.slack.blocks import respond_blocks

    email = _email("personal", "m-badge-1")
    result = _surface(email, Bucket.RESPOND)
    blocks = respond_blocks(result, show_account=False)
    texts = [str(b) for b in blocks]
    assert not any("📬" in t for t in texts)


def test_respond_blocks_badge_when_multi_account():
    from mail_agent.slack.blocks import respond_blocks

    email = _email("work", "m-badge-2")
    result = _surface(email, Bucket.RESPOND)
    blocks = respond_blocks(result, show_account=True)
    # badge block should contain the account name
    badge = next(
        (b for b in blocks if b.get("type") == "context"),
        None,
    )
    assert badge is not None
    element_text = badge["elements"][0]["text"]
    assert "work" in element_text


def test_digest_blocks_account_prefix_when_show_account():
    from mail_agent.slack.blocks import digest_blocks

    # Use distinct subjects so items don't collapse into a group.
    emails = [
        EmailMessage(
            id=MessageId(f"m-{i}"),
            thread_id=ThreadId(f"t-{i}"),
            account=AccountName("personal"),
            from_email="test@example.com",
            from_name=None,
            to=["me@example.com"],
            subject=f"Subject {i}",
            snippet="Snippet",
            headers={},
            received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for i in range(3)
    ]
    results = [_surface(e, Bucket.NOTIFY) for e in emails]
    blocks = digest_blocks(results, show_account=True)
    # Account now shown as "📬 personal" (emoji + name) in section metadata line.
    section_texts = [
        b["text"]["text"] for b in blocks if b.get("type") == "section"
    ]
    assert all("📬 personal" in t for t in section_texts)


def test_search_results_blocks_shows_account_in_context():
    from unittest.mock import MagicMock

    from mail_agent.slack.blocks import search_results_blocks

    hit = MagicMock()
    hit.subject = "Hello"
    hit.from_email = "foo@bar.com"
    hit.snippet = "snippet"
    hit.thread_id = "t-1"
    hit.account = "work"
    hit.received_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    blocks = search_results_blocks("test", "from:foo", [hit], show_account=True)
    ctx_block = next(
        (b for b in blocks if b.get("type") == "context" and "work" in str(b)),
        None,
    )
    assert ctx_block is not None


# ── _resolve_accounts validation ─────────────────────────────────────────────


def test_resolve_accounts_none_returns_none(monkeypatch):
    from mail_agent.cli import _resolve_accounts

    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,work")
    assert _resolve_accounts(None) is None


def test_resolve_accounts_valid_names(monkeypatch):
    from mail_agent.cli import _resolve_accounts

    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,work")
    result = _resolve_accounts(["work"])
    assert result == ["work"]


def test_resolve_accounts_unknown_exits(monkeypatch):
    import pytest
    import click

    from mail_agent.cli import _resolve_accounts

    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")
    with pytest.raises((SystemExit, click.exceptions.Exit)):
        _resolve_accounts(["nonexistent"])

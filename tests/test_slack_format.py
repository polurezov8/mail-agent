from __future__ import annotations

from datetime import datetime, timezone

from mail_agent.slack.format import (
    ResultGroup,
    clean_snippet,
    clean_subject,
    group_results,
    relative_time,
)


# ─── clean_subject ────────────────────────────────────────────────────────────


def test_clean_subject_strips_calendar_date_suffix():
    s = "Declined: ⚡️ Nibble MTPO Planning @ Mon Jun 15, 2026 1pm - 1:50pm (EEST) (Dmytro Poluriezov)"
    assert clean_subject(s) == "Declined: ⚡️ Nibble MTPO Planning"


def test_clean_subject_strips_updated_invitation_prefix():
    s = "Updated invitation: Product bets focus group @ Tue May 26, 2026 12pm - 1:20pm (EEST) (Dmytro Poluriezov)"
    assert clean_subject(s) == "Product bets focus group"


def test_clean_subject_keeps_declined_prefix():
    s = "Declined: ⚡️ Nibble MTPO Planning (Dmytro Poluriezov)"
    assert clean_subject(s).startswith("Declined:")


def test_clean_subject_strips_attendee_suffix():
    s = "Declined: ⚡️ Nibble MTPO Planning (Dmytro Poluriezov)"
    assert "Poluriezov" not in clean_subject(s)


def test_clean_subject_plain_subject_unchanged():
    s = "Hello from Jane"
    assert clean_subject(s) == s


def test_clean_subject_strips_invitation_prefix_only():
    s = "Invitation: Team All Hands"
    assert clean_subject(s) == "Team All Hands"


def test_clean_subject_keeps_non_calendar_trailing_parenthetical():
    """Only strip attendee parenthetical when the subject is identifiably calendar."""
    assert clean_subject("Invoice (PayPal)") == "Invoice (PayPal)"
    assert clean_subject("Project update (Action Required)") == "Project update (Action Required)"
    assert clean_subject("Hello (Alice Smith)") == "Hello (Alice Smith)"


def test_clean_subject_strips_attendee_after_action_prefix():
    """Action prefix flags the subject as calendar → attendee stripped."""
    assert clean_subject("Declined: Project Sync (Alice Smith)") == "Declined: Project Sync"
    assert clean_subject("Canceled: 1:1 (Bob Jones)") == "Canceled: 1:1"


def test_group_key_does_not_collapse_distinct_qualifiers():
    """Regression: Invoice (PayPal) and Invoice (Stripe) must not collide."""
    from mail_agent.models import (
        AccountName,
        Bucket,
        EmailMessage,
        MessageId,
        RuleName,
        SurfaceResult,
        ThreadId,
        TriageDecision,
    )
    from mail_agent.slack.format import group_key

    def _s(subject: str):
        return SurfaceResult(
            email=EmailMessage(
                id=MessageId(f"m-{subject[:8]}"),
                thread_id=ThreadId("t-1"),
                account=AccountName("work"),
                from_email="x@y.com",
                subject=subject,
                snippet="",
                headers={},
                received_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
            ),
            decision=TriageDecision(
                bucket=Bucket("ignore"),
                rule_name=RuleName("r1"),
                reasoning="r",
                confidence=0.9,
                source="header_rule",
            ),
        )

    assert group_key(_s("Invoice (PayPal)")) != group_key(_s("Invoice (Stripe)"))


# ─── clean_snippet ────────────────────────────────────────────────────────────


def test_clean_snippet_extracts_decline_note():
    s = (
        '⚡️ Nibble MTPO Planning Olesia Pshenychna has declined this invitation with a note: '
        '"Відхилено, оскільки я на місці" Join with Google Meet Meeting link '
        'meet.google.com/poi-rmyx-dhi Join by'
    )
    result = clean_snippet(s)
    assert result == 'Declined — "Відхилено, оскільки я на місці"'


def test_clean_snippet_strips_meet_boilerplate():
    s = "Product bets focus group Join with Google Meet – You have been invited to attend"
    result = clean_snippet(s)
    assert "Join with Google Meet" not in result
    assert "Product bets focus group" in result


def test_clean_snippet_strips_join_by_tail():
    s = "Some useful content Join by phone +1-234-567"
    result = clean_snippet(s)
    assert "Join by" not in result
    assert "useful content" in result


def test_clean_snippet_plain_snippet_unchanged():
    s = "Hi, can we chat later?"
    assert clean_snippet(s) == s


def test_clean_snippet_strips_meeting_link():
    s = "Details here Meeting link meet.google.com/abc-def and more stuff"
    result = clean_snippet(s)
    assert "meet.google.com" not in result


def test_clean_snippet_keeps_join_by_in_prose():
    """Regression: 'Please join by Friday' is prose, not Meet boilerplate."""
    s = "Please join by Friday for the kickoff."
    result = clean_snippet(s)
    assert "join by Friday" in result
    assert "kickoff" in result


def test_clean_snippet_keeps_join_by_date_in_invoice():
    s = "Payment due — please pay by 5pm. Join by Monday for the next round."
    result = clean_snippet(s)
    assert "Join by Monday" in result


# ─── relative_time ────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


def _dt(secs_ago: int) -> datetime:
    from datetime import timedelta
    return _NOW - timedelta(seconds=secs_ago)


def test_relative_time_just_now():
    assert relative_time(_dt(30), now=_NOW) == "just now"


def test_relative_time_minutes():
    assert relative_time(_dt(300), now=_NOW) == "5m ago"


def test_relative_time_hours():
    assert relative_time(_dt(7200), now=_NOW) == "2h ago"


def test_relative_time_yesterday():
    assert relative_time(_dt(86500), now=_NOW) == "yesterday"


def test_relative_time_older():
    from datetime import timedelta
    old = _NOW - timedelta(days=5)
    result = relative_time(old, now=_NOW)
    # Should be a date string like "May 16"
    assert "May" in result or result[0].isdigit() or result[:3].isalpha()


def test_relative_time_naive_dt():
    naive = datetime(2026, 5, 21, 11, 0, 0)  # 1h before _NOW, no tz
    assert relative_time(naive, now=_NOW) == "1h ago"


# ─── group_results ────────────────────────────────────────────────────────────


def _make_surface(subject: str, bucket: str = "ignore", account: str = "work", from_email: str = "x@y.com"):
    from mail_agent.models import (
        AccountName,
        Bucket,
        EmailMessage,
        MessageId,
        RuleName,
        SurfaceResult,
        ThreadId,
        TriageDecision,
    )
    return SurfaceResult(
        email=EmailMessage(
            id=MessageId(f"m-{subject[:8]}"),
            thread_id=ThreadId("t-1"),
            account=AccountName(account),
            from_email=from_email,
            subject=subject,
            snippet="snippet",
            headers={},
            received_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
        ),
        decision=TriageDecision(
            bucket=Bucket(bucket),
            rule_name=RuleName("r1"),
            reasoning="reason",
            confidence=0.9,
            source="header_rule",
        ),
    )


def test_group_results_collapses_identical_subjects():
    r1 = _make_surface("Declined: ⚡️ Nibble MTPO Planning (Alice Smith)")
    r2 = _make_surface("Declined: ⚡️ Nibble MTPO Planning @ Mon Jun 15, 2026 1pm - 1:50pm (EEST) (Bob Jones)")
    grouped = group_results([r1, r2])
    assert len(grouped) == 1
    assert isinstance(grouped[0], ResultGroup)
    assert len(grouped[0].members) == 2


def test_group_results_singleton_stays_single():
    r1 = _make_surface("Hello from Jane")
    grouped = group_results([r1])
    assert len(grouped) == 1
    from mail_agent.models import SurfaceResult
    assert isinstance(grouped[0], SurfaceResult)


def test_group_results_different_subjects_not_merged():
    r1 = _make_surface("Declined: Nibble MTPO Planning")
    r2 = _make_surface("Updated invitation: Product bets focus group")
    grouped = group_results([r1, r2])
    assert len(grouped) == 2


def test_group_results_different_buckets_not_merged():
    r1 = _make_surface("Same Subject", bucket="ignore")
    r2 = _make_surface("Same Subject", bucket="notify")
    grouped = group_results([r1, r2])
    assert len(grouped) == 2


def test_group_results_different_accounts_not_merged():
    r1 = _make_surface("Same Subject", account="work")
    r2 = _make_surface("Same Subject", account="personal")
    grouped = group_results([r1, r2])
    assert len(grouped) == 2


def test_group_results_preserves_first_occurrence_order():
    r_a = _make_surface("Alpha subject")
    r_b1 = _make_surface("Beta subject")
    r_b2 = _make_surface("Beta subject")
    grouped = group_results([r_a, r_b1, r_b2])
    # r_a first, then the Beta group
    from mail_agent.models import SurfaceResult
    assert isinstance(grouped[0], SurfaceResult)
    assert isinstance(grouped[1], ResultGroup)

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


def test_group_results_collapses_via_thread_when_thread_id_shared():
    """When two items share a thread_id (regardless of subject differences),
    they collapse via the thread pass, not the subject pass."""
    r1 = _make_surface("Declined: ⚡️ Nibble MTPO Planning (Alice Smith)")
    r2 = _make_surface("Declined: ⚡️ Nibble MTPO Planning @ Mon Jun 15, 2026 1pm - 1:50pm (EEST) (Bob Jones)")
    grouped = group_results([r1, r2])
    assert len(grouped) == 1
    assert isinstance(grouped[0], ResultGroup)
    assert grouped[0].grouped_by == "thread"
    assert len(grouped[0].members) == 2


def test_group_results_singleton_stays_single():
    r1 = _make_surface("Hello from Jane")
    grouped = group_results([r1])
    assert len(grouped) == 1
    from mail_agent.models import SurfaceResult
    assert isinstance(grouped[0], SurfaceResult)


def test_group_results_different_subjects_not_merged():
    r1 = _make_surface_with_thread("Declined: Nibble MTPO Planning", "t-1")
    r2 = _make_surface_with_thread("Updated invitation: Product bets focus group", "t-2")
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
    r_a = _make_surface_with_thread("Alpha subject", "t-alpha")
    r_b1 = _make_surface_with_thread("Beta subject", "t-beta")
    r_b2 = _make_surface_with_thread("Beta subject", "t-beta")
    grouped = group_results([r_a, r_b1, r_b2])
    # r_a first, then the Beta group (grouped via thread pass)
    from mail_agent.models import SurfaceResult
    assert isinstance(grouped[0], SurfaceResult)
    assert isinstance(grouped[1], ResultGroup)


# ─── _normalize_subject reply/forward stripping ───────────────────────────────


def test_normalize_subject_strips_re_chain():
    from mail_agent.slack.format import _normalize_subject

    assert _normalize_subject("Re: Re: Hello") == "hello"


def test_normalize_subject_strips_fwd_chain():
    from mail_agent.slack.format import _normalize_subject

    assert _normalize_subject("Fwd: Fw: Re: Hello") == "hello"


def test_normalize_subject_strips_action_then_reply_prefix():
    """Iterative stripping: Re: peels first, exposing Declined: which then
    peels via the action-prefix regex."""
    from mail_agent.slack.format import _normalize_subject

    assert _normalize_subject("Re: Declined: 1:1") == "1:1"


def test_normalize_subject_strips_reply_then_action_prefix():
    """Reverse order: Declined: peels first, exposing Re: which then peels."""
    from mail_agent.slack.format import _normalize_subject

    assert _normalize_subject("Declined: Re: 1:1") == "1:1"


def test_normalize_subject_plain_subject_unchanged():
    from mail_agent.slack.format import _normalize_subject

    assert _normalize_subject("Project plan") == "project plan"


def test_normalize_subject_does_not_strip_re_inside_text():
    """Only leading Re:/Fwd: prefixes, not the substring 'Re' inside words."""
    from mail_agent.slack.format import _normalize_subject

    assert _normalize_subject("Recreation activities") == "recreation activities"


def test_normalize_subject_calendar_under_reply_prefix():
    """Regression (Codex P2): Re: in front of a calendar invitation must
    normalize to the same key as the plain calendar subject — otherwise
    Gmail replies on calendar invites never group via the subject-fallback
    pass."""
    from mail_agent.slack.format import _normalize_subject

    plain = _normalize_subject(
        "Updated invitation: Team Sync @ Mon May 26, 2026 1pm - 2pm (EEST)"
    )
    replied = _normalize_subject(
        "Re: Updated invitation: Team Sync @ Mon May 26, 2026 1pm - 2pm (EEST)"
    )
    assert plain == replied == "team sync"


def test_normalize_subject_decline_with_attendee_under_reply_prefix():
    """Regression (Codex P2): Re: + Declined with attendee parenthetical
    must normalize identically to the plain Declined variant — the
    attendee suffix has to be stripped in both cases."""
    from mail_agent.slack.format import _normalize_subject

    plain = _normalize_subject("Declined: Foo (Alice Smith)")
    replied = _normalize_subject("Re: Declined: Foo (Alice Smith)")
    assert plain == replied == "foo"


def test_normalize_subject_fwd_chain_under_reply_prefix():
    """Multiple reply layers in front of an invitation: still normalize the
    same as the plain calendar subject."""
    from mail_agent.slack.format import _normalize_subject

    plain = _normalize_subject(
        "Invitation: Standup @ Tue Jun 1, 2026 10am - 10:30am (EEST)"
    )
    fwd = _normalize_subject(
        "Fwd: Re: Invitation: Standup @ Tue Jun 1, 2026 10am - 10:30am (EEST)"
    )
    assert plain == fwd == "standup"


# ─── thread_key ───────────────────────────────────────────────────────────────


def test_thread_key_includes_account_bucket_and_thread_id():
    from mail_agent.slack.format import thread_key

    s = _make_surface("Hello")  # default account="work", bucket="ignore"
    key = thread_key(s)
    assert key == ("work", "ignore", str(s.email.thread_id))


def test_thread_key_differs_for_distinct_threads():
    from mail_agent.models import ThreadId
    from mail_agent.slack.format import thread_key

    a = _make_surface("Subject")
    a.email.thread_id = ThreadId("t-alpha")
    b = _make_surface("Subject")
    b.email.thread_id = ThreadId("t-beta")
    assert thread_key(a) != thread_key(b)


def test_thread_key_ignores_subject_differences():
    """Same thread, different subjects (e.g., a Re: chain) → same key."""
    from mail_agent.models import ThreadId
    from mail_agent.slack.format import thread_key

    a = _make_surface("Original subject")
    a.email.thread_id = ThreadId("t-1")
    b = _make_surface("Re: Original subject")
    b.email.thread_id = ThreadId("t-1")
    assert thread_key(a) == thread_key(b)


# ─── ResultGroup.grouped_by ───────────────────────────────────────────────────


def test_result_group_default_grouped_by_is_subject():
    """Backward compat: members-only construction stays valid and defaults
    to 'subject' so existing test fixtures keep working."""
    from mail_agent.slack.format import ResultGroup

    g = ResultGroup(members=[_make_surface("Hello")])
    assert g.grouped_by == "subject"


def test_result_group_accepts_grouped_by_thread():
    from mail_agent.slack.format import ResultGroup

    g = ResultGroup(members=[_make_surface("Hello")], grouped_by="thread")
    assert g.grouped_by == "thread"


# ─── group_results: thread-then-subject ───────────────────────────────────────


def _make_surface_with_thread(subject: str, thread_id: str, **kwargs):
    from mail_agent.models import ThreadId

    s = _make_surface(subject, **kwargs)
    s.email.thread_id = ThreadId(thread_id)
    return s


def test_group_results_thread_collapses_re_chain():
    """Two messages in the same thread with Re:-style different subjects
    → one ResultGroup(grouped_by='thread')."""
    from mail_agent.slack.format import ResultGroup, group_results

    a = _make_surface_with_thread("Project plan", "t-1")
    b = _make_surface_with_thread("Re: Project plan", "t-1")
    grouped = group_results([a, b])
    assert len(grouped) == 1
    assert isinstance(grouped[0], ResultGroup)
    assert grouped[0].grouped_by == "thread"
    assert len(grouped[0].members) == 2


def test_group_results_thread_splits_distinct_threads_same_subject():
    """Two unrelated emails with the same subject but different threads
    → two SurfaceResult singletons, no group."""
    from mail_agent.models import SurfaceResult
    from mail_agent.slack.format import group_results

    a = _make_surface_with_thread("Quick question", "t-1")
    b = _make_surface_with_thread("Quick question", "t-2")
    grouped = group_results([a, b])
    assert len(grouped) == 2
    assert all(isinstance(item, SurfaceResult) for item in grouped)


def test_group_results_subject_fallback_when_no_thread_overlap():
    """Three Linear-style notifications, each its own thread but matching
    normalized subject → one ResultGroup(grouped_by='subject')."""
    from mail_agent.slack.format import ResultGroup, group_results

    items = [
        _make_surface_with_thread("Linear: CTS-1 updated", "t-1"),
        _make_surface_with_thread("Re: Linear: CTS-1 updated", "t-2"),
        _make_surface_with_thread("Fwd: Linear: CTS-1 updated", "t-3"),
    ]
    grouped = group_results(items)
    assert len(grouped) == 1
    assert isinstance(grouped[0], ResultGroup)
    assert grouped[0].grouped_by == "subject"
    assert len(grouped[0].members) == 3


def test_group_results_thread_wins_over_subject():
    """A 2-member thread group AND a 3-member subject group (different
    threads) both render — thread group ordered by its first occurrence."""
    from mail_agent.slack.format import ResultGroup, group_results

    # Thread pair, subject "Foo"
    t_a = _make_surface_with_thread("Foo", "t-shared")
    t_b = _make_surface_with_thread("Re: Foo", "t-shared")
    # Subject trio, distinct threads, subject "Bar"
    s_1 = _make_surface_with_thread("Bar", "t-1")
    s_2 = _make_surface_with_thread("Bar", "t-2")
    s_3 = _make_surface_with_thread("Bar", "t-3")

    grouped = group_results([t_a, s_1, t_b, s_2, s_3])
    assert len(grouped) == 2
    # First-occurrence ordering: t_a (idx 0) → thread group first.
    assert isinstance(grouped[0], ResultGroup)
    assert grouped[0].grouped_by == "thread"
    assert len(grouped[0].members) == 2
    assert isinstance(grouped[1], ResultGroup)
    assert grouped[1].grouped_by == "subject"
    assert len(grouped[1].members) == 3


def test_group_results_preserves_first_occurrence_order_v2():
    """Regression: ordering contract still holds after the rewrite."""
    from mail_agent.models import SurfaceResult
    from mail_agent.slack.format import ResultGroup, group_results

    alpha = _make_surface_with_thread("Alpha", "t-alpha")
    beta_1 = _make_surface_with_thread("Beta", "t-beta")
    beta_2 = _make_surface_with_thread("Beta", "t-beta")  # same thread, paired
    grouped = group_results([alpha, beta_1, beta_2])
    assert isinstance(grouped[0], SurfaceResult)  # alpha first
    assert isinstance(grouped[1], ResultGroup)
    assert grouped[1].grouped_by == "thread"

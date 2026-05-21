"""Tests for server-side grouping in digest/uncertain rendering."""

from __future__ import annotations

from datetime import datetime, timezone

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
from mail_agent.slack.blocks import digest_blocks, uncertain_ignore_blocks
from mail_agent.slack.format import ResultGroup, group_results


def _make(subject: str, bucket: Bucket = Bucket.IGNORE, account: str = "work", idx: int = 0) -> SurfaceResult:
    return SurfaceResult(
        email=EmailMessage(
            id=MessageId(f"m{idx}"),
            thread_id=ThreadId(f"t{idx}"),
            account=AccountName(account),
            from_email=f"sender{idx}@example.com",
            from_name=f"Sender {idx}",
            subject=subject,
            snippet=f"snippet {idx}",
            headers={},
            received_at=datetime(2026, 5, 21, 10, idx % 60, tzinfo=timezone.utc),
        ),
        decision=TriageDecision(
            bucket=bucket,
            rule_name=RuleName("r1"),
            reasoning="r",
            confidence=0.92,
            source="header_rule",
        ),
    )


# ─── group_results logic ──────────────────────────────────────────────────────


def test_four_identical_subjects_become_one_group():
    items = [_make("Declined: ⚡️ Nibble MTPO Planning (Person 1)", idx=i) for i in range(4)]
    grouped = group_results(items)
    assert len(grouped) == 1
    assert isinstance(grouped[0], ResultGroup)
    assert len(grouped[0].members) == 4


def test_mixed_subjects_four_and_one():
    same = [_make("Declined: ⚡️ Nibble Planning", idx=i) for i in range(4)]
    other = _make("Updated invitation: Product bets focus group", idx=99)
    grouped = group_results(same + [other])
    assert len(grouped) == 2
    assert isinstance(grouped[0], ResultGroup)
    assert len(grouped[0].members) == 4
    assert isinstance(grouped[1], SurfaceResult)


def test_exactly_three_collapses():
    """Subject threshold is 3: two same-subject items stay separate,
    three collapse into a group."""
    r1 = _make("Declined: Meeting X", idx=0)
    r2 = _make("Declined: Meeting X", idx=1)
    r3 = _make("Declined: Meeting X", idx=2)
    grouped = group_results([r1, r2, r3])
    assert len(grouped) == 1
    assert isinstance(grouped[0], ResultGroup)


def test_exactly_two_does_not_collapse():
    """Two same-subject items with distinct thread IDs stay as singletons
    (subject threshold is 3, not 2)."""
    r1 = _make("Declined: Meeting X", idx=0)
    r2 = _make("Declined: Meeting X", idx=1)
    grouped = group_results([r1, r2])
    assert len(grouped) == 2
    assert all(isinstance(g, SurfaceResult) for g in grouped)


# ─── digest_blocks integration ────────────────────────────────────────────────


def test_digest_four_identical_renders_one_group_card():
    items = [
        _make("Declined: ⚡️ Nibble MTPO Planning (Person)", bucket=Bucket.NOTIFY, idx=i)
        for i in range(4)
    ]
    blocks = digest_blocks(items)
    # 1 header + 1 grouped card (4 blocks) = 5
    assert len(blocks) == 5
    # Rich text list for sub-items
    rich = next((b for b in blocks if b.get("type") == "rich_text"), None)
    assert rich is not None


def test_digest_mixed_renders_group_plus_singleton():
    from mail_agent.models import ThreadId

    # Two items sharing a thread_id → thread group (≥2 threshold).
    same = [
        _make("Declined: ⚡️ Nibble Planning", bucket=Bucket.NOTIFY, idx=i) for i in range(2)
    ]
    same[0].email.thread_id = ThreadId("t-shared")
    same[1].email.thread_id = ThreadId("t-shared")
    solo = _make("Separate topic", bucket=Bucket.NOTIFY, idx=99)
    blocks = digest_blocks(same + [solo])
    # 1 header + 1 thread group (4 blocks) + 1 singleton (2 blocks) = 7
    assert len(blocks) == 7


def test_digest_all_different_stays_flat():
    items = [_make(f"Subject {i}", bucket=Bucket.NOTIFY, idx=i) for i in range(3)]
    blocks = digest_blocks(items)
    # 1 header + 3 × (section + actions) = 7
    assert len(blocks) == 7


# ─── uncertain_ignore_blocks integration ─────────────────────────────────────


def test_uncertain_four_identical_collapses():
    items = [_make("Declined: Nibble MTPO Planning (X)", idx=i) for i in range(4)]
    blocks = uncertain_ignore_blocks(items)
    # 1 header + 1 subtitle context + 1 group (4 blocks) = 6
    assert len(blocks) == 6
    rich = next((b for b in blocks if b.get("type") == "rich_text"), None)
    assert rich is not None


def test_digest_blocks_thread_groups_render_with_thread_marker():
    """Two messages in the same thread → digest_blocks renders one grouped
    card carrying the 🧵 thread marker (not 📦 similar)."""
    import json

    a = _make("Project plan", bucket=Bucket.NOTIFY, idx=0)
    a.email.thread_id = ThreadId("t-1")
    b = _make("Re: Project plan", bucket=Bucket.NOTIFY, idx=1)
    b.email.thread_id = ThreadId("t-1")

    blocks = digest_blocks([a, b], show_account=False)
    blob = json.dumps(blocks, ensure_ascii=False)
    assert "🧵 thread" in blob
    assert "2 messages" in blob

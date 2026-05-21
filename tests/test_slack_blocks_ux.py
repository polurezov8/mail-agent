"""UX-focused block tests: no model names, compact layout, overflow structure."""

from __future__ import annotations

import json
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
from mail_agent.slack.blocks import (
    digest_blocks,
    grouped_row_blocks,
    respond_blocks,
    uncertain_ignore_blocks,
)
from mail_agent.slack.format import ResultGroup


def _surface(subject="Hello", bucket=Bucket.NOTIFY, source="llm_fast", model="claude-haiku-4-5", account="personal"):
    email = EmailMessage(
        id=MessageId("m1"),
        thread_id=ThreadId("t1"),
        account=AccountName(account),
        from_email="alice@example.com",
        from_name="Alice",
        subject=subject,
        snippet="Some content Join with Google Meet Meeting link meet.google.com/abc Join by",
        headers={},
        received_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
    )
    decision = TriageDecision(
        bucket=bucket,
        rule_name=RuleName("test_rule"),
        reasoning="test reason",
        confidence=0.92,
        source=source,
        model=model,
    )
    return SurfaceResult(email=email, decision=decision)


# ─── Model name not exposed to user ───────────────────────────────────────────


def test_compact_row_no_model_name():
    from mail_agent.slack.blocks import _compact_row_text

    s = _surface()
    text = _compact_row_text(s.email, s.decision)
    assert "claude-haiku" not in text
    assert "claude-sonnet" not in text


def test_digest_no_model_name_in_output():
    blob = json.dumps(digest_blocks([_surface()]))
    assert "claude-haiku" not in blob


def test_respond_no_model_name_in_output():
    blob = json.dumps(respond_blocks(_surface(bucket=Bucket.RESPOND)))
    assert "claude-haiku" not in blob


def test_uncertain_no_model_name_in_output():
    s = _surface(bucket=Bucket.IGNORE)
    blob = json.dumps(uncertain_ignore_blocks([s]))
    assert "claude-haiku" not in blob


# ─── Sender in headline ────────────────────────────────────────────────────────


def test_compact_row_sender_in_headline():
    from mail_agent.slack.blocks import _compact_row_text

    s = _surface()
    text = _compact_row_text(s.email, s.decision)
    # First line should start with bold sender
    first_line = text.split("\n")[0]
    assert "*Alice*" in first_line


# ─── Meet boilerplate stripped from snippet ────────────────────────────────────


def test_compact_row_snippet_stripped():
    from mail_agent.slack.blocks import _compact_row_text

    s = _surface()
    text = _compact_row_text(s.email, s.decision)
    assert "Join with Google Meet" not in text
    assert "meet.google.com" not in text


# ─── Overflow structure ────────────────────────────────────────────────────────


def _find_overflow(blocks):
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("type") == "overflow":
                    return el
    return None


def test_digest_row_has_overflow_not_correct_bucket_button():
    blocks = digest_blocks([_surface(subject="Unique subject A"), _surface(subject="Unique subject B")])
    # Two different subjects → 2 singletons

    def _find(blocks, action_id):
        for b in blocks:
            for el in b.get("elements", []) if b.get("type") == "actions" else []:
                if el.get("action_id") == action_id:
                    return el
        return None

    assert _find(blocks, "correct_bucket") is None
    assert _find_overflow(blocks) is not None
    assert _find_overflow(blocks)["action_id"] == "row_overflow"


def test_respond_blocks_open_is_primary():
    blocks = respond_blocks(_surface(bucket=Bucket.RESPOND))
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("action_id") == "open_gmail":
                    assert el.get("style") == "primary"
                    return
    raise AssertionError("open_gmail primary button not found")


def test_digest_row_mark_read_is_primary():
    blocks = digest_blocks([_surface(subject="Only item")])
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("action_id") == "mark_read":
                    assert el.get("style") == "danger"
                    return
    raise AssertionError("mark_read danger button not found in digest singleton")


# ─── Uncertain subtitle wording ────────────────────────────────────────────────


def test_uncertain_subtitle_wording():
    s = _surface(bucket=Bucket.IGNORE)
    blocks = uncertain_ignore_blocks([s])
    blob = json.dumps(blocks)
    assert "Auto-archive blocked" in blob
    assert "gate held" not in blob


def test_uncertain_header_no_item_suffix():
    s = _surface(bucket=Bucket.IGNORE)
    blocks = uncertain_ignore_blocks([s])
    header_text = blocks[0]["text"]["text"]
    assert "item(s)" not in header_text


def test_digest_header_no_item_suffix():
    blocks = digest_blocks([_surface()])
    header_text = blocks[0]["text"]["text"]
    assert "item(s)" not in header_text


# ─── grouped_row_blocks ────────────────────────────────────────────────────────


def test_grouped_row_has_mark_all_read_button():
    s1 = _surface(subject="Declined: Nibble Planning (Alice Smith)")
    s2 = _surface(subject="Declined: Nibble Planning (Bob Jones)")
    group = ResultGroup(members=[s1, s2])
    blocks = grouped_row_blocks(group)

    def _find(blocks, action_id):
        for b in blocks:
            for el in b.get("elements", []) if b.get("type") == "actions" else []:
                if el.get("action_id") == action_id:
                    return el
        return None

    btn = _find(blocks, "mark_read_bulk")
    assert btn is not None
    assert "2" in btn["text"]["text"]


def test_grouped_row_rich_text_list():
    s1 = _surface(subject="Declined: Nibble Planning")
    s2 = _surface(subject="Declined: Nibble Planning")
    group = ResultGroup(members=[s1, s2])
    blocks = grouped_row_blocks(group)
    rich_block = next((b for b in blocks if b.get("type") == "rich_text"), None)
    assert rich_block is not None
    list_el = rich_block["elements"][0]
    assert list_el["type"] == "rich_text_list"
    assert len(list_el["elements"]) == 2


def test_grouped_row_mark_all_value_format():
    s1 = _surface(subject="Declined: Nibble Planning")
    s1.email.id = MessageId("id-one")
    s2 = _surface(subject="Declined: Nibble Planning")
    s2.email.id = MessageId("id-two")
    group = ResultGroup(members=[s1, s2])
    blocks = grouped_row_blocks(group)
    for b in blocks:
        for el in b.get("elements", []) if b.get("type") == "actions" else []:
            if el.get("action_id") == "mark_read_bulk":
                assert "personal:id-one" in el["value"]
                assert "personal:id-two" in el["value"]
                return
    raise AssertionError("mark_read_bulk not found")


# ─── grouped_by marker ────────────────────────────────────────────────────────


def test_grouped_row_thread_marker():
    """Thread-grouped cards show 🧵 thread · N messages, not 📦 similar."""
    import json

    from mail_agent.slack.blocks import grouped_row_blocks
    from mail_agent.slack.format import ResultGroup

    s1 = _surface(subject="Project plan")
    s2 = _surface(subject="Re: Project plan")
    group = ResultGroup(members=[s1, s2], grouped_by="thread")
    blob = json.dumps(grouped_row_blocks(group), ensure_ascii=False)
    assert "🧵 thread" in blob
    assert "2 messages" in blob
    assert "similar" not in blob


def test_grouped_row_subject_marker_unchanged():
    """Subject-grouped cards keep the original 📦 N similar marker."""
    import json

    from mail_agent.slack.blocks import grouped_row_blocks
    from mail_agent.slack.format import ResultGroup

    s1 = _surface(subject="Linear: CTS-1 updated")
    s2 = _surface(subject="Linear: CTS-2 updated")
    group = ResultGroup(members=[s1, s2], grouped_by="subject")
    blob = json.dumps(grouped_row_blocks(group), ensure_ascii=False)
    assert "📦" in blob
    assert "similar" in blob
    assert "🧵" not in blob

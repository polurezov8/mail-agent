from __future__ import annotations

import json

from mail_agent.models import Bucket, SurfaceResult
from mail_agent.slack.blocks import (
    digest_blocks,
    respond_blocks,
    review_blocks,
)


def _find(blocks, action_id):
    """Return the first interactive element with action_id matching."""
    for b in blocks:
        for el in b.get("elements", []) if b.get("type") == "actions" else []:
            if el.get("action_id") == action_id:
                return el
        if b.get("type") == "section":
            acc = b.get("accessory")
            if acc and acc.get("action_id") == action_id:
                return acc
    return None


def _find_overflow_option_value(blocks, text_contains: str) -> str | None:
    """Find a value in row_overflow options whose label contains text_contains."""
    for b in blocks:
        if b.get("type") != "actions":
            continue
        for el in b.get("elements", []):
            if el.get("type") == "overflow" and el.get("action_id") == "row_overflow":
                for opt in el.get("options", []):
                    if text_contains.lower() in opt.get("text", {}).get("text", "").lower():
                        return opt.get("value")
    return None


def test_respond_blocks_have_required_actions(sample_email, decision_factory):
    surface = SurfaceResult(
        email=sample_email(),
        decision=decision_factory(bucket=Bucket.RESPOND, source="llm_fast", model="m1"),
    )
    blocks = respond_blocks(surface)
    assert blocks[0]["type"] == "header"
    # Open in Gmail is the primary button on respond cards
    assert _find(blocks, "open_gmail") is not None
    assert _find(blocks, "mark_read") is not None
    # Wrong bucket lives in overflow (action_id=row_overflow)
    assert _find_overflow_option_value(blocks, "Wrong bucket") is not None
    # Dedicated correct_bucket button no longer present
    assert _find(blocks, "correct_bucket") is None


def test_respond_blocks_hide_model_id(sample_email, decision_factory):
    surface = SurfaceResult(
        email=sample_email(),
        decision=decision_factory(
            bucket=Bucket.RESPOND,
            source="llm_fast",
            model="claude-haiku-4-5",
        ),
    )
    blob = json.dumps(respond_blocks(surface))
    assert "claude-haiku-4-5" not in blob


def test_digest_blocks_two_identical_subjects_collapse(sample_email, decision_factory):
    surface = SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.NOTIFY))
    blocks = digest_blocks([surface, surface])
    assert blocks[0]["type"] == "header"
    # Two items sharing a thread_id → one grouped card (collapsed via thread pass) (section + rich_text + context + actions)
    assert len(blocks) == 5  # 1 header + 4 group blocks


def test_digest_blocks_two_different_subjects_stay_separate(sample_email, decision_factory):
    from mail_agent.models import ThreadId

    s1 = SurfaceResult(
        email=sample_email(subject="Alpha meeting", thread_id=ThreadId("t-alpha")),
        decision=decision_factory(bucket=Bucket.NOTIFY),
    )
    s2 = SurfaceResult(
        email=sample_email(subject="Beta standup", thread_id=ThreadId("t-beta")),
        decision=decision_factory(bucket=Bucket.NOTIFY),
    )
    blocks = digest_blocks([s1, s2])
    # 1 header + 2 × (section + actions) = 5
    assert len(blocks) == 5
    assert blocks[1]["type"] == "section"
    assert blocks[2]["type"] == "actions"


def test_digest_split_chunks_large_batches(sample_email, decision_factory):
    from mail_agent.slack.blocks import split_digest

    surface = SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.NOTIFY))
    big = [surface] * 50
    chunks = split_digest(big)
    # 50 / 24 = 3 chunks
    assert len(chunks) == 3
    assert sum(len(c) for c in chunks) == 50


def test_review_blocks_have_wrong_bucket_button():
    candidates = [
        dict(
            id=1,
            account="personal",
            message_id="m1",
            from_email="x@y.com",
            subject="Test",
            source="llm_fast",
            confidence=0.88,
            rule_name=None,
            model="claude-haiku-test",
        )
    ]
    blocks = review_blocks(candidates)
    assert _find(blocks, "correct_bucket") is not None
    assert _find(blocks, "mark_read") is None  # already marked; no mark-read here


def test_review_blocks_hide_model_id():
    candidates = [
        dict(
            id=1,
            account="personal",
            message_id="m1",
            from_email="x@y.com",
            subject="Test",
            source="llm_fast",
            confidence=0.88,
            rule_name=None,
            model="claude-haiku-test",
        )
    ]
    blob = json.dumps(review_blocks(candidates))
    assert "claude-haiku-test" not in blob


def test_overflow_value_encodes_account_message_bucket(sample_email, decision_factory):
    surface = SurfaceResult(
        email=sample_email(id="abc"),
        decision=decision_factory(bucket=Bucket.NOTIFY),
    )
    blocks = digest_blocks([surface])
    # Single item → singleton row, Wrong bucket in overflow
    value = _find_overflow_option_value(blocks, "Wrong bucket")
    assert value == "personal:abc:notify"

from __future__ import annotations

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


def test_respond_blocks_have_required_buttons(sample_email, decision_factory):
    surface = SurfaceResult(
        email=sample_email(),
        decision=decision_factory(bucket=Bucket.RESPOND, source="llm_fast", model="m1"),
    )
    blocks = respond_blocks(surface)
    assert blocks[0]["type"] == "header"
    assert _find(blocks, "mark_read") is not None
    assert _find(blocks, "open_gmail") is not None
    assert _find(blocks, "correct_bucket") is not None


def test_respond_blocks_show_model_when_present(sample_email, decision_factory):
    surface = SurfaceResult(
        email=sample_email(),
        decision=decision_factory(
            bucket=Bucket.RESPOND,
            source="llm_fast",
            model="claude-haiku-4-5",
        ),
    )
    import json

    blob = json.dumps(respond_blocks(surface))
    assert "claude-haiku-4-5" in blob


def test_digest_blocks_emit_row_per_item(sample_email, decision_factory):
    surface = SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.NOTIFY))
    blocks = digest_blocks([surface, surface])
    # 1 header + 2 rows × (section + actions) = 1 + 4 = 5
    assert len(blocks) == 5
    assert blocks[0]["type"] == "header"
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
    # Each rendered chunk stays under Slack's 50-block hard cap
    for chunk in chunks:
        assert 1 + 2 * len(chunk) <= 50


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


def test_correct_bucket_value_encodes_account_message_bucket(sample_email, decision_factory):
    surface = SurfaceResult(
        email=sample_email(id="abc"),
        decision=decision_factory(bucket=Bucket.NOTIFY),
    )
    blocks = digest_blocks([surface])
    btn = _find(blocks, "correct_bucket")
    assert btn["value"] == "personal:abc:notify"

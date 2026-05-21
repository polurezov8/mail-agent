from __future__ import annotations

import json

from mail_agent.slack.modals import correction_modal


def test_correction_modal_carries_metadata():
    modal = correction_modal(
        account="personal",
        message_id="m1",
        from_email="x@y.com",
        subject="Test subject",
        original_bucket="ignore",
    )
    assert modal["type"] == "modal"
    assert modal["callback_id"] == "correction_modal"
    meta = json.loads(modal["private_metadata"])
    assert meta["account"] == "personal"
    assert meta["message_id"] == "m1"
    assert meta["original_bucket"] == "ignore"


def test_correction_modal_includes_three_buckets():
    modal = correction_modal(
        account="personal",
        message_id="m1",
        from_email="x@y.com",
        subject="x",
        original_bucket="ignore",
    )
    block_ids = [b.get("block_id") for b in modal["blocks"]]
    assert "bucket_block" in block_ids
    bucket_block = next(b for b in modal["blocks"] if b.get("block_id") == "bucket_block")
    values = {o["value"] for o in bucket_block["element"]["options"]}
    assert values == {"ignore", "notify", "respond"}


def test_correction_modal_has_apply_and_note_inputs():
    modal = correction_modal(
        account="personal",
        message_id="m1",
        from_email="x@y.com",
        subject="x",
        original_bucket="ignore",
    )
    block_ids = [b.get("block_id") for b in modal["blocks"]]
    assert "apply_block" in block_ids
    assert "note_block" in block_ids

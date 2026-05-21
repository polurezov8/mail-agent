from __future__ import annotations

from mail_agent.brief import build_brief
from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    MessageId,
    SurfaceResult,
)
from mail_agent.store.sqlite import (
    init_db,
    log_correction,
    log_mark_read,
    mark_processed,
)


def test_brief_empty_db(isolated_db):
    summary = build_brief(hours=24)
    assert summary.processed_total == 0
    assert summary.marked_read == 0


def test_brief_counts_processed_and_marked(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(id=MessageId("a")),
                decision=decision_factory(bucket=Bucket.IGNORE, rule_name="github"),
            ),
            SurfaceResult(
                email=sample_email(id=MessageId("b"), from_email="ceo@x.com"),
                decision=decision_factory(bucket=Bucket.RESPOND, rule_name=None),
            ),
        ]
    )
    log_mark_read(
        [
            AutoMarkResult(
                email=sample_email(id=MessageId("a")),
                decision=decision_factory(rule_name="github"),
            )
        ],
        dry_run=False,
    )
    summary = build_brief(hours=24)
    assert summary.processed_total == 2
    assert summary.bucket_counts["ignore"] == 1
    assert summary.bucket_counts["respond"] == 1
    assert summary.marked_read == 1
    assert summary.rule_counts == [("github", 1)]


def test_brief_collects_notable_respond(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(
                    id=MessageId("r1"),
                    from_email="ceo@x.com",
                    subject="Q3 roadmap",
                ),
                decision=decision_factory(bucket=Bucket.RESPOND, rule_name=None),
            )
        ]
    )
    summary = build_brief(hours=24)
    assert len(summary.notable_respond) == 1
    assert summary.notable_respond[0].from_email == "ceo@x.com"
    assert summary.notable_respond[0].subject == "Q3 roadmap"


def test_brief_counts_uncertain_band(isolated_db, sample_email, decision_factory):
    init_db()
    log_mark_read(
        [
            AutoMarkResult(
                email=sample_email(id=MessageId("u1")),
                decision=decision_factory(source="llm_fast", confidence=0.88),
            )
        ],
        dry_run=False,
    )
    summary = build_brief(hours=24)
    assert summary.uncertain_band == 1


def test_brief_counts_corrections(isolated_db):
    init_db()
    log_correction(
        account="personal",
        message_id="x",
        original_bucket="ignore",
        original_rule=None,
        corrected_bucket="respond",
        note=None,
        from_email="z@y.com",
        subject="x",
        apply_to_sender=False,
    )
    summary = build_brief(hours=24)
    assert summary.corrections_recent == 1

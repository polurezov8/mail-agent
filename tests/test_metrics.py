from __future__ import annotations

from mail_agent.models import AutoMarkResult, Bucket, MessageId, SurfaceResult
from mail_agent.store.sqlite import init_db, log_correction, log_mark_read, mark_processed
from mail_agent.store.stats import compute_metrics


def test_metrics_empty_db(isolated_db):
    m = compute_metrics(period="week")
    assert m.total_processed == 0
    assert m.bucket_counts == {"ignore": 0, "notify": 0, "respond": 0}
    assert m.rule_top == []
    assert m.daily == []


def test_metrics_buckets_and_sources(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(id=MessageId("a")),
                decision=decision_factory(bucket=Bucket.IGNORE, rule_name="github"),
            ),
            SurfaceResult(
                email=sample_email(id=MessageId("b")),
                decision=decision_factory(
                    bucket=Bucket.RESPOND, rule_name=None, source="llm_fast"
                ),
            ),
        ]
    )
    m = compute_metrics(period="week")
    assert m.total_processed == 2
    assert m.bucket_counts["ignore"] == 1
    assert m.bucket_counts["respond"] == 1
    assert m.source_counts.get("header_rule", 0) == 1
    assert m.source_counts.get("llm_fast", 0) == 1


def test_metrics_top_rules_sorted(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(id=MessageId(f"a{i}")),
                decision=decision_factory(rule_name="newsletters"),
            )
            for i in range(3)
        ]
        + [
            SurfaceResult(
                email=sample_email(id=MessageId(f"b{i}")),
                decision=decision_factory(rule_name="github"),
            )
            for i in range(5)
        ]
    )
    m = compute_metrics(period="week")
    assert m.rule_top[0].name == "github"
    assert m.rule_top[0].count == 5
    assert m.rule_top[1].name == "newsletters"


def test_metrics_includes_marks_and_corrections(isolated_db, sample_email, decision_factory):
    init_db()
    log_mark_read(
        [
            AutoMarkResult(
                email=sample_email(id=MessageId("m1")),
                decision=decision_factory(),
            )
        ],
        dry_run=False,
    )
    log_correction(
        account="personal",
        message_id="m1",
        original_bucket="ignore",
        original_rule=None,
        corrected_bucket="respond",
        note=None,
        from_email="x@y.com",
        subject="x",
        apply_to_sender=True,
    )
    m = compute_metrics(period="week")
    assert m.marked_read == 1
    assert m.corrections == 1


def test_metrics_top_senders_auto_marked(isolated_db, sample_email, decision_factory):
    init_db()
    log_mark_read(
        [
            AutoMarkResult(
                email=sample_email(id=MessageId(f"a{i}"), from_email="bulk@x.com"),
                decision=decision_factory(),
            )
            for i in range(7)
        ]
        + [
            AutoMarkResult(
                email=sample_email(id=MessageId(f"b{i}"), from_email="single@y.com"),
                decision=decision_factory(),
            )
            for i in range(2)
        ],
        dry_run=False,
    )
    m = compute_metrics(period="week")
    assert m.top_senders_auto_marked[0].from_email == "bulk@x.com"
    assert m.top_senders_auto_marked[0].count == 7


def test_metrics_period_all(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(id=MessageId("old")),
                decision=decision_factory(),
            )
        ]
    )
    m = compute_metrics(period="all")
    assert m.period == "all"
    assert m.since is None
    assert m.total_processed == 1

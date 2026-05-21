from __future__ import annotations

from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    MessageId,
    SurfaceResult,
)
from mail_agent.store.sqlite import init_db, log_mark_read, mark_processed
from mail_agent.store.stats import get_recent_audit, get_status


def test_status_when_db_empty(isolated_db):
    snap = get_status()
    assert snap.total_processed == 0
    assert snap.marked_read_total == 0
    assert snap.last_processed_at is None


def test_status_counts(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(email=sample_email(id=MessageId("a")), decision=decision_factory()),
            SurfaceResult(
                email=sample_email(id=MessageId("b")),
                decision=decision_factory(bucket=Bucket.NOTIFY),
            ),
        ]
    )
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("a")), decision=decision_factory())],
        dry_run=False,
    )
    snap = get_status()
    assert snap.total_processed == 2
    assert snap.marked_read_total == 1
    assert snap.bucket_counts["ignore"] >= 1


def test_recent_audit_orders_newest_first(isolated_db, sample_email, decision_factory):
    init_db()
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("old")), decision=decision_factory())],
        dry_run=False,
    )
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("new")), decision=decision_factory())],
        dry_run=False,
    )
    rows = get_recent_audit(limit=5)
    # newest first: the second log call appears first in result
    assert rows[0].marked_at >= rows[-1].marked_at

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mail_agent.models import (
    AccountName,
    AutoMarkResult,
    Bucket,
    MessageId,
    SurfaceResult,
)
from mail_agent.store.mail_store import MailStore, MarkReadExample, ProcessedExample
from mail_agent.store.sqlite import init_db, log_mark_read, mark_processed


def test_db_exists_false_when_no_file(isolated_db):
    # isolated_db points GMAIL_DB_PATH at a path that doesn't exist yet.
    store = MailStore()
    assert store.db_exists is False


def test_db_exists_true_after_init(isolated_db):
    init_db()
    store = MailStore()
    assert store.db_exists is True


def test_context_manager_opens_and_closes(isolated_db):
    init_db()
    with MailStore() as store:
        assert store.conn is not None
        store.total_processed()  # no error
    # connection released
    assert store._conn is None  # noqa: SLF001


def test_bucket_counts_windowed(isolated_db, sample_email, decision_factory):
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
    with MailStore() as store:
        unfiltered = store.bucket_counts()
        assert unfiltered.get("ignore", 0) == 1
        assert unfiltered.get("notify", 0) == 1


def test_mark_read_count_excludes_dry_run(isolated_db, sample_email, decision_factory):
    init_db()
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("real")), decision=decision_factory())],
        dry_run=False,
    )
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("dry")), decision=decision_factory())],
        dry_run=True,
    )
    with MailStore() as store:
        assert store.total_mark_read() == 1


def test_account_filter(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(id=MessageId("p1"), account=AccountName("personal")),
                decision=decision_factory(),
            ),
            SurfaceResult(
                email=sample_email(id=MessageId("w1"), account=AccountName("work")),
                decision=decision_factory(),
            ),
        ]
    )
    with MailStore() as store:
        all_counts = store.bucket_counts()
        work_only = store.bucket_counts(accounts=["work"])
        assert sum(all_counts.values()) == 2
        assert sum(work_only.values()) == 1


def test_recent_by_bucket_returns_typed_examples(isolated_db, sample_email, decision_factory):
    init_db()
    mark_processed(
        [
            SurfaceResult(
                email=sample_email(
                    id=MessageId("r"),
                    from_email="someone@example.com",
                    subject="Reply please",
                ),
                decision=decision_factory(bucket=Bucket.RESPOND),
            ),
        ]
    )
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with MailStore() as store:
        items = store.recent_by_bucket(since=since, bucket="respond")
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ProcessedExample)
    assert item.bucket == "respond"
    assert item.from_email == "someone@example.com"
    assert item.subject == "Reply please"


def test_recent_mark_read_newest_first(isolated_db, sample_email, decision_factory):
    init_db()
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("old")), decision=decision_factory())],
        dry_run=False,
    )
    log_mark_read(
        [AutoMarkResult(email=sample_email(id=MessageId("new")), decision=decision_factory())],
        dry_run=False,
    )
    with MailStore() as store:
        rows = store.recent_mark_read(limit=5)
    assert all(isinstance(r, MarkReadExample) for r in rows)
    assert len(rows) == 2
    assert rows[0].marked_at >= rows[-1].marked_at


def test_in_memory_db_supported():
    """Smoke-test the :memory: path used in fast unit tests."""
    store = MailStore(db_path=":memory:")
    assert store.db_exists is True
    with store:
        # Empty in-memory DB: no tables. total_processed should raise OperationalError
        # unless init_db is applied to this connection. That's expected behavior; the
        # contract is: caller is responsible for schema setup on :memory: dbs.
        # Here we only assert the lifecycle works.
        assert store.conn is not None

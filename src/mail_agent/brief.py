"""Daily Brief: narrative summary of the last N hours of agent activity.

Different from the notify digest (per-mail rows). Brief = aggregate counts +
notable items, in one Slack post.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from .store.mail_store import MailStore, ProcessedExample


class NotableItem(BaseModel):
    from_email: str
    subject: str
    bucket: str
    rule_name: str | None
    source: str
    confidence: float


class BriefSummary(BaseModel):
    since: str  # ISO timestamp
    hours: int
    processed_total: int
    bucket_counts: dict[str, int]
    rule_counts: list[tuple[str, int]]  # most-marked rule first
    marked_read: int
    notable_respond: list[NotableItem]
    notable_notify: list[NotableItem]
    uncertain_band: int  # LLM-classified ignore at 0.85–0.95 not yet reviewed
    corrections_recent: int


def _to_notable(ex: ProcessedExample) -> NotableItem:
    return NotableItem(
        from_email=ex.from_email,
        subject=ex.subject,
        bucket=ex.bucket,
        rule_name=ex.rule_name,
        source=ex.source,
        confidence=ex.confidence,
    )


def build_brief(hours: int = 24, accounts: list[str] | None = None) -> BriefSummary:
    since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since_dt.isoformat()

    with MailStore() as store:
        if not store.db_exists:
            return BriefSummary(
                since=since_iso,
                hours=hours,
                processed_total=0,
                bucket_counts={},
                rule_counts=[],
                marked_read=0,
                notable_respond=[],
                notable_notify=[],
                uncertain_band=0,
                corrections_recent=0,
            )

        bucket_counts = store.bucket_counts(since=since_iso, accounts=accounts)
        rule_counts = store.rule_counts(since=since_iso, accounts=accounts, limit=10)
        marked_read = store.mark_read_count(since=since_iso, accounts=accounts)
        notable_respond = [
            _to_notable(ex)
            for ex in store.recent_by_bucket(
                since=since_iso, bucket="respond", accounts=accounts, limit=5
            )
        ]
        notable_notify = [
            _to_notable(ex)
            for ex in store.recent_by_bucket(
                since=since_iso, bucket="notify", accounts=accounts, limit=5
            )
        ]
        uncertain_band = store.uncertain_band_count(since=since_iso, accounts=accounts)
        corrections_recent = store.correction_count(since=since_iso)

    return BriefSummary(
        since=since_iso,
        hours=hours,
        processed_total=sum(bucket_counts.values()),
        bucket_counts=bucket_counts,
        rule_counts=rule_counts,
        marked_read=marked_read,
        notable_respond=notable_respond,
        notable_notify=notable_notify,
        uncertain_band=uncertain_band,
        corrections_recent=corrections_recent,
    )

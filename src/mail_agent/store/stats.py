from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ..models import Bucket
from .mail_store import MailStore


class StatusSnapshot(BaseModel):
    total_processed: int
    last_processed_at: str | None
    processed_today: int
    bucket_counts: dict[str, int]
    marked_read_total: int
    marked_read_today: int


class AuditEntry(BaseModel):
    marked_at: str
    from_email: str
    subject: str
    rule_name: str | None
    source: str
    confidence: float
    dry_run: bool


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _empty_status() -> StatusSnapshot:
    return StatusSnapshot(
        total_processed=0,
        last_processed_at=None,
        processed_today=0,
        bucket_counts={b.value: 0 for b in Bucket},
        marked_read_total=0,
        marked_read_today=0,
    )


def get_status() -> StatusSnapshot:
    with MailStore() as store:
        if not store.db_exists:
            return _empty_status()

        today = _today_iso()
        bucket_counts = {b.value: 0 for b in Bucket}
        bucket_counts.update(store.bucket_counts())
        return StatusSnapshot(
            total_processed=store.total_processed(),
            last_processed_at=store.last_processed_at(),
            processed_today=sum(store.bucket_counts(since=today).values()),
            bucket_counts=bucket_counts,
            marked_read_total=store.total_mark_read(),
            marked_read_today=store.mark_read_count(since=today),
        )


class TopRule(BaseModel):
    name: str
    count: int


class TopSender(BaseModel):
    from_email: str
    count: int


class DailyCount(BaseModel):
    date: str  # YYYY-MM-DD
    total: int


class MetricsSnapshot(BaseModel):
    period: str  # "day" | "week" | "month" | "all"
    since: str | None  # ISO; None when period=all
    total_processed: int
    bucket_counts: dict[str, int]
    source_counts: dict[str, int]
    rule_top: list[TopRule]
    marked_read: int
    corrections: int
    uncertain_band: int
    daily: list[DailyCount]
    top_senders_auto_marked: list[TopSender]


_PERIOD_HOURS = {
    "day": 24,
    "week": 24 * 7,
    "month": 24 * 30,
    "all": None,
}


def _empty_metrics(period: str, since_iso: str | None) -> MetricsSnapshot:
    return MetricsSnapshot(
        period=period,
        since=since_iso,
        total_processed=0,
        bucket_counts={b.value: 0 for b in Bucket},
        source_counts={},
        rule_top=[],
        marked_read=0,
        corrections=0,
        uncertain_band=0,
        daily=[],
        top_senders_auto_marked=[],
    )


def compute_metrics(
    period: str = "week",
    accounts: list[str] | None = None,
) -> MetricsSnapshot:
    if period not in _PERIOD_HOURS:
        period = "week"
    hours = _PERIOD_HOURS[period]
    since_dt = (
        None if hours is None else datetime.now(timezone.utc) - timedelta(hours=hours)
    )
    since_iso = since_dt.isoformat() if since_dt else None

    with MailStore() as store:
        if not store.db_exists:
            return _empty_metrics(period, since_iso)

        bucket_counts = {b.value: 0 for b in Bucket}
        bucket_counts.update(store.bucket_counts(since=since_iso, accounts=accounts))
        total_processed = sum(bucket_counts.values())

        source_counts = store.source_counts(since=since_iso, accounts=accounts)
        rule_top = [
            TopRule(name=name, count=count)
            for name, count in store.rule_counts(since=since_iso, accounts=accounts, limit=10)
        ]
        marked_read = store.mark_read_count(since=since_iso, accounts=accounts)
        # Corrections — cross-account by sender, intentionally not scoped to accounts.
        corrections = store.correction_count(since=since_iso)
        uncertain_band = store.uncertain_band_count(since=since_iso, accounts=accounts)

        # Daily activity (last N days where N = period length, or last 30 for "all").
        daily_n = 30 if period == "all" else max(1, (hours or 0) // 24 or 1)
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=daily_n)
        ).isoformat()
        daily = [
            DailyCount(date=day, total=count)
            for day, count in store.daily_processed_counts(since=cutoff_iso, accounts=accounts)
        ]

        top_senders = [
            TopSender(from_email=email, count=count)
            for email, count in store.top_senders_auto_marked(
                since=since_iso, accounts=accounts, limit=5
            )
        ]

    return MetricsSnapshot(
        period=period,
        since=since_iso,
        total_processed=total_processed,
        bucket_counts=bucket_counts,
        source_counts=source_counts,
        rule_top=rule_top,
        marked_read=marked_read,
        corrections=corrections,
        uncertain_band=uncertain_band,
        daily=daily,
        top_senders_auto_marked=top_senders,
    )


def get_recent_audit(limit: int = 10) -> list[AuditEntry]:
    with MailStore() as store:
        if not store.db_exists:
            return []
        return [
            AuditEntry(
                marked_at=ex.marked_at,
                from_email=ex.from_email,
                subject=ex.subject,
                rule_name=ex.rule_name,
                source=ex.source,
                confidence=ex.confidence,
                dry_run=ex.dry_run,
            )
            for ex in store.recent_mark_read(limit=limit)
        ]

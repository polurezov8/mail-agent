from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ..models import Bucket
from .sqlite import _conn, _db_path


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


def get_status() -> StatusSnapshot:
    if not _db_path().exists():
        return StatusSnapshot(
            total_processed=0,
            last_processed_at=None,
            processed_today=0,
            bucket_counts={b.value: 0 for b in Bucket},
            marked_read_total=0,
            marked_read_today=0,
        )

    today = _today_iso()
    with _conn() as conn:
        total_processed = conn.execute("SELECT COUNT(*) FROM processed_messages").fetchone()[0]
        last = conn.execute("SELECT MAX(processed_at) FROM processed_messages").fetchone()[0]
        processed_today = conn.execute(
            "SELECT COUNT(*) FROM processed_messages WHERE processed_at >= ?",
            (today,),
        ).fetchone()[0]
        bucket_rows = conn.execute(
            "SELECT bucket, COUNT(*) FROM processed_messages GROUP BY bucket"
        ).fetchall()
        marked_total = conn.execute(
            "SELECT COUNT(*) FROM mark_read_audit WHERE dry_run = 0"
        ).fetchone()[0]
        marked_today = conn.execute(
            "SELECT COUNT(*) FROM mark_read_audit WHERE dry_run = 0 AND marked_at >= ?",
            (today,),
        ).fetchone()[0]

    bucket_counts = {b.value: 0 for b in Bucket}
    bucket_counts.update(dict(bucket_rows))
    return StatusSnapshot(
        total_processed=total_processed,
        last_processed_at=last,
        processed_today=processed_today,
        bucket_counts=bucket_counts,
        marked_read_total=marked_total,
        marked_read_today=marked_today,
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


def compute_metrics(period: str = "week") -> MetricsSnapshot:
    if period not in _PERIOD_HOURS:
        period = "week"
    hours = _PERIOD_HOURS[period]
    since_dt = (
        None if hours is None else datetime.now(timezone.utc) - timedelta(hours=hours)
    )
    since_iso = since_dt.isoformat() if since_dt else None

    if not _db_path().exists():
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

    where = "WHERE processed_at >= ?" if since_iso else ""
    args = (since_iso,) if since_iso else ()

    with _conn() as conn:
        # Totals + buckets
        bucket_rows = conn.execute(
            f"SELECT bucket, COUNT(*) FROM processed_messages {where} GROUP BY bucket",
            args,
        ).fetchall()
        bucket_counts = {b.value: 0 for b in Bucket}
        bucket_counts.update(dict(bucket_rows))
        total_processed = sum(bucket_counts.values())

        # Sources
        source_rows = conn.execute(
            f"SELECT source, COUNT(*) FROM processed_messages {where} GROUP BY source",
            args,
        ).fetchall()
        source_counts = dict(source_rows)

        # Top rules
        rule_rows = conn.execute(
            f"SELECT rule_name, COUNT(*) FROM processed_messages "
            f"{where + (' AND ' if where else 'WHERE ')}rule_name IS NOT NULL "
            f"GROUP BY rule_name ORDER BY COUNT(*) DESC LIMIT 10",
            args,
        ).fetchall()
        rule_top = [TopRule(name=n, count=c) for n, c in rule_rows]

        # Marked read
        mr_where = "WHERE dry_run = 0" + (" AND marked_at >= ?" if since_iso else "")
        marked_read = conn.execute(
            f"SELECT COUNT(*) FROM mark_read_audit {mr_where}",
            args,
        ).fetchone()[0]

        # Corrections
        corr_where = "WHERE corrected_at >= ?" if since_iso else ""
        corrections = conn.execute(
            f"SELECT COUNT(*) FROM corrections {corr_where}",
            args,
        ).fetchone()[0]

        # Uncertain band
        unc_where = (
            "WHERE dry_run = 0 AND source LIKE 'llm%' "
            "AND confidence >= 0.85 AND confidence < 0.95 AND reviewed_at IS NULL"
            + (" AND marked_at >= ?" if since_iso else "")
        )
        uncertain_band = conn.execute(
            f"SELECT COUNT(*) FROM mark_read_audit {unc_where}",
            args,
        ).fetchone()[0]

        # Daily activity (last N days where N = period length, or last 30 for "all")
        daily_n = 30 if period == "all" else max(1, hours // 24 or 1)
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=daily_n)
        ).isoformat()
        daily_rows = conn.execute(
            "SELECT substr(processed_at, 1, 10) AS day, COUNT(*) "
            "FROM processed_messages WHERE processed_at >= ? "
            "GROUP BY day ORDER BY day",
            (cutoff_iso,),
        ).fetchall()
        daily = [DailyCount(date=d, total=c) for d, c in daily_rows]

        # Top auto-marked senders
        sender_where = "WHERE dry_run = 0 AND from_email IS NOT NULL AND from_email != ''" + (
            " AND marked_at >= ?" if since_iso else ""
        )
        sender_rows = conn.execute(
            f"SELECT from_email, COUNT(*) FROM mark_read_audit {sender_where} "
            f"GROUP BY from_email ORDER BY COUNT(*) DESC LIMIT 5",
            args,
        ).fetchall()
        top_senders = [TopSender(from_email=e, count=c) for e, c in sender_rows]

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
    if not _db_path().exists():
        return []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT marked_at, from_email, subject, rule_name, source, confidence, dry_run "
            "FROM mark_read_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        AuditEntry(
            marked_at=marked_at,
            from_email=from_email or "",
            subject=subject or "",
            rule_name=rule_name,
            source=source,
            confidence=confidence,
            dry_run=bool(dry_run),
        )
        for marked_at, from_email, subject, rule_name, source, confidence, dry_run in rows
    ]

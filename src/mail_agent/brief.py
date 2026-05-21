"""Daily Brief: narrative summary of the last N hours of agent activity.

Different from the notify digest (per-mail rows). Brief = aggregate counts +
notable items, in one Slack post.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from .store.sqlite import _conn, _db_path


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


def build_brief(hours: int = 24, accounts: list[str] | None = None) -> BriefSummary:
    since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since_dt.isoformat()

    if not _db_path().exists():
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

    # Build account filter fragment for processed_messages queries
    if accounts:
        ph = ",".join("?" * len(accounts))
        acct_clause = f" AND account IN ({ph})"
        acct_args = tuple(accounts)
    else:
        acct_clause = ""
        acct_args = ()

    with _conn() as conn:
        bucket_rows = conn.execute(
            f"SELECT bucket, COUNT(*) FROM processed_messages "
            f"WHERE processed_at >= ?{acct_clause} GROUP BY bucket",
            (since_iso, *acct_args),
        ).fetchall()
        bucket_counts = {b: c for b, c in bucket_rows}

        rule_rows = conn.execute(
            f"SELECT rule_name, COUNT(*) FROM processed_messages "
            f"WHERE processed_at >= ? AND rule_name IS NOT NULL{acct_clause} "
            f"GROUP BY rule_name ORDER BY COUNT(*) DESC",
            (since_iso, *acct_args),
        ).fetchall()

        marked_read = conn.execute(
            "SELECT COUNT(*) FROM mark_read_audit WHERE marked_at >= ? AND dry_run = 0"
            + acct_clause,
            (since_iso, *acct_args),
        ).fetchone()[0]

        # Skip legacy rows missing sender metadata (pre-migration) — surfacing
        # them as "(unknown)" placeholders just adds noise without signal.
        notable_respond_rows = conn.execute(
            f"SELECT rule_name, bucket, source, confidence, from_email, subject "
            f"FROM processed_messages "
            f"WHERE processed_at >= ? AND bucket = 'respond' "
            f"  AND from_email IS NOT NULL AND from_email != ''{acct_clause} "
            f"ORDER BY processed_at DESC LIMIT 5",
            (since_iso, *acct_args),
        ).fetchall()

        notable_notify_rows = conn.execute(
            f"SELECT rule_name, bucket, source, confidence, from_email, subject "
            f"FROM processed_messages "
            f"WHERE processed_at >= ? AND bucket = 'notify' "
            f"  AND from_email IS NOT NULL AND from_email != ''{acct_clause} "
            f"ORDER BY processed_at DESC LIMIT 5",
            (since_iso, *acct_args),
        ).fetchall()

        uncertain_band = conn.execute(
            "SELECT COUNT(*) FROM mark_read_audit "
            "WHERE marked_at >= ? AND dry_run = 0 "
            "AND source LIKE 'llm%' "
            "AND confidence >= 0.85 AND confidence < 0.95 "
            "AND reviewed_at IS NULL" + acct_clause,
            (since_iso, *acct_args),
        ).fetchone()[0]

        corrections_recent = conn.execute(
            "SELECT COUNT(*) FROM corrections WHERE corrected_at >= ?",
            (since_iso,),
        ).fetchone()[0]

    def _row_to_item(r):
        return NotableItem(
            from_email=r[4],
            subject=r[5] or "(no subject)",  # rare: legitimately-empty subject
            bucket=r[1],
            rule_name=r[0],
            source=r[2],
            confidence=r[3],
        )

    notable_respond = [_row_to_item(r) for r in notable_respond_rows]
    notable_notify = [_row_to_item(r) for r in notable_notify_rows]

    processed_total = sum(bucket_counts.values())
    rule_counts = [(name, count) for name, count in rule_rows][:10]

    return BriefSummary(
        since=since_iso,
        hours=hours,
        processed_total=processed_total,
        bucket_counts=bucket_counts,
        rule_counts=rule_counts,
        marked_read=marked_read,
        notable_respond=notable_respond,
        notable_notify=notable_notify,
        uncertain_band=uncertain_band,
        corrections_recent=corrections_recent,
    )

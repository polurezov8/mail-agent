from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..models import AccountName, AutoMarkResult, MessageId, TriageResult


def _db_path() -> Path:
    return Path(os.environ.get("GMAIL_DB_PATH", "./mail_agent.db"))


@contextmanager
def _conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema migrations (ALTER TABLE wrapped in try/except)."""
    for stmt in (
        "ALTER TABLE mark_read_audit ADD COLUMN reviewed_at TEXT",
        "ALTER TABLE mark_read_audit ADD COLUMN model TEXT",
        "ALTER TABLE processed_messages ADD COLUMN model TEXT",
        "ALTER TABLE processed_messages ADD COLUMN from_email TEXT",
        "ALTER TABLE processed_messages ADD COLUMN subject TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS unsubscribes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            unsubscribed_at TEXT NOT NULL,
            sender          TEXT NOT NULL,
            method          TEXT NOT NULL,
            success         INTEGER NOT NULL,
            detail          TEXT,
            UNIQUE(sender)
        )
        """
    )


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_messages (
                account     TEXT NOT NULL,
                message_id  TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                bucket      TEXT NOT NULL,
                rule_name   TEXT,
                source      TEXT NOT NULL,
                confidence  REAL NOT NULL,
                PRIMARY KEY (account, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_processed_at
                ON processed_messages(processed_at);

            CREATE TABLE IF NOT EXISTS mark_read_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account     TEXT NOT NULL,
                message_id  TEXT NOT NULL,
                marked_at   TEXT NOT NULL,
                rule_name   TEXT,
                source      TEXT NOT NULL,
                confidence  REAL NOT NULL,
                from_email  TEXT,
                subject     TEXT,
                dry_run     INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_audit_marked_at
                ON mark_read_audit(marked_at);

            CREATE TABLE IF NOT EXISTS corrections (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                corrected_at      TEXT NOT NULL,
                account           TEXT NOT NULL,
                message_id        TEXT NOT NULL,
                original_bucket   TEXT NOT NULL,
                original_rule     TEXT,
                corrected_bucket  TEXT NOT NULL,
                note              TEXT,
                from_email        TEXT,
                subject           TEXT,
                apply_to_sender   INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_corrections_at
                ON corrections(corrected_at);
            CREATE INDEX IF NOT EXISTS idx_corrections_from_email
                ON corrections(from_email);
            """
        )
        _migrate(conn)


def log_correction(
    account: str,
    message_id: str,
    original_bucket: str,
    original_rule: str | None,
    corrected_bucket: str,
    note: str | None,
    from_email: str | None,
    subject: str | None,
    apply_to_sender: bool = False,
) -> None:
    # Normalize so `sender_override` (case-insensitive query) matches on the
    # next mail from this address regardless of header casing.
    if from_email:
        from_email = from_email.strip().lower()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO corrections "
            "(corrected_at, account, message_id, original_bucket, original_rule, "
            "corrected_bucket, note, from_email, subject, apply_to_sender) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                account,
                message_id,
                original_bucket,
                original_rule,
                corrected_bucket,
                note,
                from_email,
                subject,
                1 if apply_to_sender else 0,
            ),
        )


def get_review_candidates(
    conf_min: float = 0.85,
    conf_max: float = 0.95,
    pool_size: int = 50,
) -> list[dict]:
    """Auto-marked-ignore mails where the LLM (not a header rule) was the
    classifier and confidence sits in the uncertain band — the genuine gap
    where review value is highest. Excludes already-reviewed entries."""
    if not _db_path().exists():
        return []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, account, message_id, marked_at, source, confidence, "
            "rule_name, from_email, subject, model "
            "FROM mark_read_audit "
            "WHERE dry_run = 0 "
            "  AND source LIKE 'llm%' "
            "  AND confidence >= ? AND confidence < ? "
            "  AND reviewed_at IS NULL "
            "ORDER BY marked_at DESC "
            "LIMIT ?",
            (conf_min, conf_max, pool_size),
        ).fetchall()
    cols = [
        "id",
        "account",
        "message_id",
        "marked_at",
        "source",
        "confidence",
        "rule_name",
        "from_email",
        "subject",
        "model",
    ]
    return [dict(zip(cols, row)) for row in rows]


def mark_reviewed(audit_ids: list[int]) -> None:
    if not audit_ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(audit_ids))
    with _conn() as conn:
        conn.execute(
            f"UPDATE mark_read_audit SET reviewed_at = ? WHERE id IN ({placeholders})",
            (now, *audit_ids),
        )


def list_unsubscribe_candidates(
    hours: int = 168,  # 7 days default
    limit: int = 100,
) -> list[dict]:
    """Senders auto-marked as ignore via newsletters rule (or other) within
    `hours`, that we haven't unsubscribed from yet."""
    if not _db_path().exists():
        return []
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT mra.from_email, COUNT(*) AS hits, MAX(mra.message_id) AS sample_id "
            "FROM mark_read_audit mra "
            "WHERE mra.marked_at >= ? AND mra.dry_run = 0 "
            "  AND mra.from_email IS NOT NULL AND mra.from_email != '' "
            "  AND mra.from_email NOT IN (SELECT sender FROM unsubscribes WHERE success = 1) "
            "GROUP BY mra.from_email "
            "ORDER BY hits DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return [{"from_email": r[0], "hits": r[1], "sample_message_id": r[2]} for r in rows]


def log_unsubscribe(sender: str, method: str, success: bool, detail: str = "") -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO unsubscribes "
            "(unsubscribed_at, sender, method, success, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, sender, method, 1 if success else 0, detail),
        )


def list_unsubscribes(limit: int = 50) -> list[dict]:
    if not _db_path().exists():
        return []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT unsubscribed_at, sender, method, success, detail "
            "FROM unsubscribes ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "unsubscribed_at": r[0],
            "sender": r[1],
            "method": r[2],
            "success": bool(r[3]),
            "detail": r[4],
        }
        for r in rows
    ]


def list_corrections(limit: int = 20) -> list[dict]:
    """Most recent corrections, newest first."""
    if not _db_path().exists():
        return []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, corrected_at, account, message_id, original_bucket, "
            "corrected_bucket, apply_to_sender, from_email, subject, note "
            "FROM corrections ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    cols = [
        "id",
        "corrected_at",
        "account",
        "message_id",
        "original_bucket",
        "corrected_bucket",
        "apply_to_sender",
        "from_email",
        "subject",
        "note",
    ]
    return [dict(zip(cols, row)) for row in rows]


def deactivate_correction(correction_id: int) -> bool:
    """Soft-undo: stop the correction from applying to future mail from this
    sender (clears apply_to_sender). Audit row preserved. Returns True if a
    row was changed."""
    if not _db_path().exists():
        return False
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE corrections SET apply_to_sender = 0 WHERE id = ?",
            (correction_id,),
        )
        return cur.rowcount > 0


def sender_override(from_email: str) -> str | None:
    """Most-recent corrected_bucket for a sender, if user opted to apply to sender.

    Returns the bucket name (`ignore`/`notify`/`respond`) or None.
    """
    if not from_email or not _db_path().exists():
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT corrected_bucket FROM corrections "
            "WHERE from_email = ? AND apply_to_sender = 1 "
            "ORDER BY corrected_at DESC LIMIT 1",
            (from_email.lower(),),
        ).fetchone()
    return row[0] if row else None


def log_mark_read(results: list[AutoMarkResult], dry_run: bool) -> None:
    if not results:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            r.email.account,
            r.email.id,
            now,
            r.decision.rule_name,
            r.decision.source,
            r.decision.confidence,
            r.email.from_email,
            r.email.subject,
            1 if dry_run else 0,
            r.decision.model,
        )
        for r in results
    ]
    with _conn() as conn:
        conn.executemany(
            "INSERT INTO mark_read_audit "
            "(account, message_id, marked_at, rule_name, source, confidence, "
            "from_email, subject, dry_run, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def processed_ids(account: AccountName, candidate_ids: list[MessageId]) -> set[MessageId]:
    if not candidate_ids:
        return set()
    placeholders = ",".join("?" * len(candidate_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT message_id FROM processed_messages "
            f"WHERE account = ? AND message_id IN ({placeholders})",
            [account, *candidate_ids],
        ).fetchall()
    return {MessageId(row[0]) for row in rows}


def mark_processed(results: list[TriageResult]) -> None:
    if not results:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            r.email.account,
            r.email.id,
            now,
            r.decision.bucket.value,
            r.decision.rule_name,
            r.decision.source,
            r.decision.confidence,
            r.decision.model,
            r.email.from_email,
            r.email.subject,
        )
        for r in results
    ]
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO processed_messages "
            "(account, message_id, processed_at, bucket, rule_name, source, "
            " confidence, model, from_email, subject) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

"""Read-side query module for stats and brief views.

All callers that want to read processed_messages / mark_read_audit / corrections
go through MailStore. The store owns one sqlite connection for its lifetime and
returns aggregates (dict / list of tuples) or typed example rows. Schema
knowledge — column names, WHERE-clause assembly, account/time scoping — lives
here, not in the view modules.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from .sqlite import _db_path


@dataclass(frozen=True)
class ProcessedExample:
    from_email: str
    subject: str
    bucket: str
    rule_name: str | None
    source: str
    confidence: float


@dataclass(frozen=True)
class MarkReadExample:
    marked_at: str
    from_email: str
    subject: str
    rule_name: str | None
    source: str
    confidence: float
    dry_run: bool


class MailStore:
    """Read-side store for stats + brief views.

    Use as a context manager:

        with MailStore() as store:
            counts = store.bucket_counts(since)
            rules = store.rule_counts(since)

    The constructor only records the path; the connection opens at __enter__
    and closes at __exit__. Passing db_path=':memory:' is supported for tests.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _db_path()
        # Capture file presence BEFORE opening — sqlite3.connect() creates the
        # file as a side effect, so checking after __enter__ always returns True.
        self._existed_before_open = (
            True if str(self._db_path) == ":memory:" else self._db_path.exists()
        )
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def __enter__(self) -> "MailStore":
        self._open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _open(self) -> None:
        if self._conn is not None:
            return
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Underlying connection. Exposed for tests that seed fixtures."""
        if self._conn is None:
            raise RuntimeError("MailStore not opened. Use as a context manager.")
        return self._conn

    @property
    def db_exists(self) -> bool:
        """True if the db file existed at construction time.

        Captured before __enter__ because sqlite3.connect() creates the file.
        Callers use this to short-circuit on a fresh install with no data yet.
        """
        return self._existed_before_open

    # ------------------------------------------------------------------ #
    # Aggregates — snapshot (no window filter)
    # ------------------------------------------------------------------ #

    def total_processed(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM processed_messages").fetchone()
        return int(row[0])

    def last_processed_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(processed_at) FROM processed_messages"
        ).fetchone()
        return row[0]

    def total_mark_read(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM mark_read_audit WHERE dry_run = 0"
        ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------ #
    # Aggregates — windowed
    # ------------------------------------------------------------------ #

    def bucket_counts(
        self,
        since: str | None = None,
        accounts: list[str] | None = None,
    ) -> dict[str, int]:
        where, args = _processed_where(since, accounts)
        rows = self.conn.execute(
            f"SELECT bucket, COUNT(*) FROM processed_messages {where} GROUP BY bucket",
            args,
        ).fetchall()
        return {bucket: count for bucket, count in rows}

    def source_counts(
        self,
        since: str | None,
        accounts: list[str] | None = None,
    ) -> dict[str, int]:
        where, args = _processed_where(since, accounts)
        rows = self.conn.execute(
            f"SELECT source, COUNT(*) FROM processed_messages {where} GROUP BY source",
            args,
        ).fetchall()
        return {source: count for source, count in rows}

    def rule_counts(
        self,
        since: str | None,
        accounts: list[str] | None = None,
        limit: int | None = 10,
    ) -> list[tuple[str, int]]:
        where, args = _processed_where(since, accounts, extra=["rule_name IS NOT NULL"])
        limit_clause = f" LIMIT {int(limit)}" if limit else ""
        rows = self.conn.execute(
            f"SELECT rule_name, COUNT(*) FROM processed_messages "
            f"{where} GROUP BY rule_name ORDER BY COUNT(*) DESC{limit_clause}",
            args,
        ).fetchall()
        return [(name, int(count)) for name, count in rows]

    def mark_read_count(
        self,
        since: str | None = None,
        accounts: list[str] | None = None,
    ) -> int:
        where, args = _mark_read_where(since, accounts, dry_run=False)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM mark_read_audit {where}",
            args,
        ).fetchone()
        return int(row[0])

    def correction_count(self, since: str | None = None) -> int:
        if since:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM corrections WHERE corrected_at >= ?",
                (since,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM corrections").fetchone()
        return int(row[0])

    def uncertain_band_count(
        self,
        since: str | None,
        accounts: list[str] | None = None,
    ) -> int:
        """LLM-classified at 0.85–0.95 confidence, auto-marked, not yet reviewed."""
        extra = [
            "source LIKE 'llm%'",
            "confidence >= 0.85",
            "confidence < 0.95",
            "reviewed_at IS NULL",
        ]
        where, args = _mark_read_where(since, accounts, dry_run=False, extra=extra)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM mark_read_audit {where}",
            args,
        ).fetchone()
        return int(row[0])

    def daily_processed_counts(
        self,
        since: str,
        accounts: list[str] | None = None,
    ) -> list[tuple[str, int]]:
        """Returns (YYYY-MM-DD, count) tuples ordered by day ascending."""
        where, args = _processed_where(since, accounts)
        rows = self.conn.execute(
            f"SELECT substr(processed_at, 1, 10) AS day, COUNT(*) "
            f"FROM processed_messages {where} GROUP BY day ORDER BY day",
            args,
        ).fetchall()
        return [(day, int(count)) for day, count in rows]

    def top_senders_auto_marked(
        self,
        since: str | None,
        accounts: list[str] | None = None,
        limit: int = 5,
    ) -> list[tuple[str, int]]:
        extra = ["from_email IS NOT NULL", "from_email != ''"]
        where, args = _mark_read_where(since, accounts, dry_run=False, extra=extra)
        rows = self.conn.execute(
            f"SELECT from_email, COUNT(*) FROM mark_read_audit {where} "
            f"GROUP BY from_email ORDER BY COUNT(*) DESC LIMIT ?",
            (*args, int(limit)),
        ).fetchall()
        return [(email, int(count)) for email, count in rows]

    # ------------------------------------------------------------------ #
    # Examples (raw rows)
    # ------------------------------------------------------------------ #

    def recent_by_bucket(
        self,
        since: str,
        bucket: str,
        accounts: list[str] | None = None,
        limit: int = 5,
    ) -> list[ProcessedExample]:
        """Recent processed_messages matching bucket, with sender metadata.

        Skips legacy rows missing sender metadata (pre-migration) — surfacing them
        as "(unknown)" placeholders adds noise without signal.
        """
        extra = [
            "bucket = ?",
            "from_email IS NOT NULL",
            "from_email != ''",
        ]
        where, args = _processed_where(since, accounts, extra=extra)
        # The extra "bucket = ?" placeholder needs the bucket arg threaded in.
        # _processed_where keeps args in the order (since, ...accounts, ...extra-bound)
        # but our "extra" with bucket = ? requires a manual bind; do it explicitly here.
        rows = self.conn.execute(
            f"SELECT rule_name, bucket, source, confidence, from_email, subject "
            f"FROM processed_messages {where} "
            f"ORDER BY processed_at DESC LIMIT ?",
            (*_bind_processed_args(since, accounts, extra_args=[bucket]), int(limit)),
        ).fetchall()
        return [
            ProcessedExample(
                from_email=from_email,
                subject=subject or "(no subject)",
                bucket=row_bucket,
                rule_name=rule_name,
                source=source,
                confidence=float(confidence),
            )
            for rule_name, row_bucket, source, confidence, from_email, subject in rows
        ]

    def recent_mark_read(self, limit: int = 10) -> list[MarkReadExample]:
        rows = self.conn.execute(
            "SELECT marked_at, from_email, subject, rule_name, source, confidence, dry_run "
            "FROM mark_read_audit ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [
            MarkReadExample(
                marked_at=marked_at,
                from_email=from_email or "",
                subject=subject or "",
                rule_name=rule_name,
                source=source,
                confidence=float(confidence),
                dry_run=bool(dry_run),
            )
            for (marked_at, from_email, subject, rule_name, source, confidence, dry_run)
            in rows
        ]


# ---------------------------------------------------------------------- #
# Internal WHERE-clause builders. Keep schema knowledge in one place.
# ---------------------------------------------------------------------- #


def _processed_where(
    since: str | None,
    accounts: list[str] | None,
    extra: list[str] | None = None,
) -> tuple[str, tuple]:
    """Build WHERE clause + args for processed_messages.

    Returns ("", ()) when no filters. Args order: since (if any),
    then each account, then each extra predicate's bound value if any.
    Extra predicates with their own '?' placeholders bind their args from
    the caller via the helper _bind_processed_args, NOT from here.
    """
    conds: list[str] = []
    args: list = []
    if since is not None:
        conds.append("processed_at >= ?")
        args.append(since)
    if accounts:
        ph = ",".join("?" * len(accounts))
        conds.append(f"account IN ({ph})")
        args.extend(accounts)
    if extra:
        conds.extend(extra)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, tuple(args)


def _bind_processed_args(
    since: str | None,
    accounts: list[str] | None,
    extra_args: list | None = None,
) -> tuple:
    """Bind args including extras that contain their own placeholders."""
    args: list = []
    if since is not None:
        args.append(since)
    if accounts:
        args.extend(accounts)
    if extra_args:
        args.extend(extra_args)
    return tuple(args)


def _mark_read_where(
    since: str | None,
    accounts: list[str] | None,
    *,
    dry_run: bool | None = None,
    extra: list[str] | None = None,
) -> tuple[str, tuple]:
    """Build WHERE clause + args for mark_read_audit.

    dry_run=False adds "dry_run = 0" (the common case — excludes dry runs from
    real counts). dry_run=None omits the predicate entirely.
    """
    conds: list[str] = []
    args: list = []
    if dry_run is False:
        conds.append("dry_run = 0")
    if since is not None:
        conds.append("marked_at >= ?")
        args.append(since)
    if accounts:
        ph = ",".join("?" * len(accounts))
        conds.append(f"account IN ({ph})")
        args.extend(accounts)
    if extra:
        conds.extend(extra)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, tuple(args)

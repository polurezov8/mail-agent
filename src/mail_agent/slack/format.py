"""Pure text cleaners and result grouping for Slack rendering.

All functions are side-effect-free and independently testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from ..models import SurfaceResult


# ─── Subject cleaner ──────────────────────────────────────────────────────────

# Strip "Updated invitation: " / "Invitation: " / "New invitation: " prefixes.
# "Declined:" / "Canceled:" are kept — they carry action semantics.
_RE_INVITATION_PREFIX = re.compile(
    r"^(Updated invitation|New invitation|Invitation|Re-sent invitation):\s*",
    re.IGNORECASE,
)

# Strip Google Calendar date suffix: " @ Tue May 26, 2026 12pm - 1:20pm (EEST)"
# Optional timezone in parens at end of time range.
_RE_CAL_DATE_SUFFIX = re.compile(
    r"\s*@\s+\w+\s+\w+\s+\d{1,2},?\s+\d{4}\s+\d{1,2}(:\d{2})?"
    r"(am|pm)\s*[-–]\s*\d{1,2}(:\d{2})?(am|pm)(\s*\([^)]+\))?",
    re.IGNORECASE,
)

# Strip trailing attendee parenthetical: " (Dmytro Poluriezov)" — calendar-only.
_RE_ATTENDEE_SUFFIX = re.compile(r"\s+\([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{2,40}\)\s*$")

# Calendar action prefix kept in the subject (we don't strip these — they
# carry semantics — but their presence flags the message as a calendar item.
_RE_CALENDAR_ACTION_PREFIX = re.compile(
    r"^(Declined|Canceled|Cancelled|Accepted|Tentative|Updated):",
    re.IGNORECASE,
)


def clean_subject(subject: str) -> str:
    """Strip Google Calendar noise from a subject.

    The attendee parenthetical is stripped ONLY when the subject is otherwise
    identifiable as a calendar item — by invitation prefix, calendar action
    prefix (Declined/Canceled/…), or a calendar date suffix. Without that
    gate, legitimate qualifiers like `Invoice (PayPal)` would be removed and
    distinct subjects would collapse under the same group key.
    """
    s = subject.strip()

    had_invitation = bool(_RE_INVITATION_PREFIX.match(s))
    s = _RE_INVITATION_PREFIX.sub("", s)

    had_action = bool(_RE_CALENDAR_ACTION_PREFIX.match(s))

    pre_date = s
    s = _RE_CAL_DATE_SUFFIX.sub("", s)
    had_date = s != pre_date

    if had_invitation or had_action or had_date:
        s = _RE_ATTENDEE_SUFFIX.sub("", s)

    return s.strip()


# ─── Snippet cleaner ──────────────────────────────────────────────────────────

# Decline with note: extract just the quoted note.
_RE_DECLINE_NOTE = re.compile(
    r'.*has declined this invitation with a note:\s*"([^"]+)".*',
    re.DOTALL | re.IGNORECASE,
)

_RE_MEET_JOIN = re.compile(r"Join with Google Meet\s*[-–]?\s*", re.IGNORECASE)
_RE_MEET_LINK = re.compile(r"Meeting link\s+\S+\s*", re.IGNORECASE)
# Calendar boilerplate is specifically `Join by phone` / `Join by video` —
# never just `Join by Friday`. Gate on the medium word to avoid clobbering
# legitimate prose like "Please join by Friday".
_RE_JOIN_BY = re.compile(
    r"\s*Join by\s+(phone|video|telephone)\b.*$", re.IGNORECASE | re.DOTALL
)


def clean_snippet(snippet: str) -> str:
    s = snippet.strip()

    # Calendar decline: surface just the quoted note.
    m = _RE_DECLINE_NOTE.match(s)
    if m:
        note = m.group(1).strip()
        return f'Declined — "{note}"' if note else "Declined (no note)"

    s = _RE_MEET_JOIN.sub("", s)
    s = _RE_MEET_LINK.sub("", s)
    s = _RE_JOIN_BY.sub("", s)
    s = " ".join(s.split())
    return s.strip()


# ─── Relative timestamp ────────────────────────────────────────────────────────


def relative_time(received_at: datetime, now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    secs = int((now - received_at).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 2 * 86400:
        return "yesterday"
    return received_at.strftime("%b %-d")


# ─── Result grouping ──────────────────────────────────────────────────────────

_RE_ACTION_PREFIX = re.compile(
    r"^(declined|canceled|cancelled|accepted|tentative):\s*",
    re.IGNORECASE,
)

# "Re:", "Re: Re:", "Fwd:", "Fw:", "Re-sent:" chains at the start of a subject.
# Iterated by _normalize_subject — single application strips one layer.
_RE_REPLY_PREFIX = re.compile(
    r"^(re|fwd?|fw|re-sent)\s*:\s*",
    re.IGNORECASE,
)


def _normalize_subject(subject: str) -> str:
    """Strip reply prefixes, run calendar cleanup, then loop-strip residual
    action/reply prefixes until stable.

    Reply prefixes (`Re:`, `Fwd:`, …) are peeled BEFORE `clean_subject` so its
    invitation/action/date regexes can match the underlying calendar text at
    position 0, and the attendee parenthetical gets stripped. Without the
    pre-peel, `Re: Updated invitation: X @ …` normalizes differently from
    `Updated invitation: X @ …` and the two never collide in the
    subject-fallback grouping pass.
    """
    s = subject.strip()

    # Pre-peel: reply prefixes only. Exposes the underlying calendar prefix
    # to clean_subject.
    while True:
        prev = s
        s = _RE_REPLY_PREFIX.sub("", s)
        if s == prev:
            break

    s = clean_subject(s).lower().strip()

    # Post-peel: handles interleavings like `Declined: Re: 1:1` where one
    # prefix only becomes the new leading token after the other peels.
    while True:
        prev = s
        s = _RE_ACTION_PREFIX.sub("", s)
        s = _RE_REPLY_PREFIX.sub("", s)
        if s == prev:
            break
    return s


def thread_key(result: SurfaceResult) -> tuple[str, str, str]:
    """Group-by-thread key: (account, bucket, thread_id)."""
    return (
        result.email.account,
        result.decision.bucket.value,
        result.email.thread_id,
    )


def group_key(result: SurfaceResult) -> tuple[str, str, str]:
    """Stable key for grouping: (account, bucket, normalized_subject)."""
    return (
        result.email.account,
        result.decision.bucket.value,
        _normalize_subject(result.email.subject),
    )


@dataclass
class ResultGroup:
    members: list[SurfaceResult] = field(default_factory=list)
    grouped_by: Literal["thread", "subject"] = "subject"

    @property
    def representative(self) -> SurfaceResult:
        """Newest member for display."""
        return max(self.members, key=lambda r: r.email.received_at)


def group_results(results: list[SurfaceResult]) -> list[SurfaceResult | ResultGroup]:
    """Two-pass grouping: thread-id first, then normalized subject.

    Pass 1: bucket by (account, bucket, thread_id). Threads with ≥2 members
    become ResultGroup(grouped_by="thread"); singletons feed pass 2.

    Pass 2: bucket the remaining singletons by (account, bucket,
    normalized_subject). Subjects with ≥3 members become
    ResultGroup(grouped_by="subject"); the rest emit as SurfaceResult.
    (Subject is a weaker signal than thread — require more evidence.)

    Ordering: each emitted item's position in the output is the original
    index of its first member.
    """
    SUBJECT_GROUP_MIN = 3

    # Pass 1: thread bucketing.
    thread_buckets: dict[tuple, list[SurfaceResult]] = {}
    thread_first_idx: dict[tuple, int] = {}
    for idx, r in enumerate(results):
        k = thread_key(r)
        if k not in thread_buckets:
            thread_buckets[k] = []
            thread_first_idx[k] = idx
        thread_buckets[k].append(r)

    # Emitted items keyed by their first-occurrence index in `results`.
    emitted: dict[int, SurfaceResult | ResultGroup] = {}
    # Carry per-member original idx through to pass 2.
    leftover: list[tuple[int, SurfaceResult]] = []

    for k, members in thread_buckets.items():
        if len(members) >= 2:
            emitted[thread_first_idx[k]] = ResultGroup(
                members=members, grouped_by="thread"
            )
        else:
            leftover.append((thread_first_idx[k], members[0]))

    # Pass 2: subject bucketing on leftovers only. Keep per-member idx so
    # below-threshold members can be emitted at their own original positions.
    subject_buckets: dict[tuple, list[tuple[int, SurfaceResult]]] = {}
    subject_first_idx: dict[tuple, int] = {}
    for original_idx, r in leftover:
        k = group_key(r)
        if k not in subject_buckets:
            subject_buckets[k] = []
            subject_first_idx[k] = original_idx
        subject_buckets[k].append((original_idx, r))

    for k, indexed_members in subject_buckets.items():
        if len(indexed_members) >= SUBJECT_GROUP_MIN:
            emitted[subject_first_idx[k]] = ResultGroup(
                members=[m for _, m in indexed_members], grouped_by="subject"
            )
        else:
            for original_idx, r in indexed_members:
                emitted[original_idx] = r

    return [emitted[idx] for idx in sorted(emitted)]

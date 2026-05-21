"""Pure text cleaners and result grouping for Slack rendering.

All functions are side-effect-free and independently testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


def _normalize_subject(subject: str) -> str:
    s = clean_subject(subject).lower().strip()
    s = _RE_ACTION_PREFIX.sub("", s)
    return s


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

    @property
    def representative(self) -> SurfaceResult:
        """Newest member for display."""
        return max(self.members, key=lambda r: r.email.received_at)


def group_results(results: list[SurfaceResult]) -> list[SurfaceResult | ResultGroup]:
    """Group results by account+bucket+normalized_subject.

    Groups of 1 are returned as plain SurfaceResult.
    Groups of 2+ are wrapped in ResultGroup.
    Original ordering is preserved by first-occurrence index.
    """
    buckets: dict[tuple, list[SurfaceResult]] = {}
    order: list[tuple] = []
    for r in results:
        k = group_key(r)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(r)

    out: list[SurfaceResult | ResultGroup] = []
    for k in order:
        members = buckets[k]
        if len(members) >= 2:
            out.append(ResultGroup(members=members))
        else:
            out.append(members[0])
    return out

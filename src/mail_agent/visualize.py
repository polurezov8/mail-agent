"""Tiny visualization helpers — unicode bars and sparklines.

Kept dependency-free so we can render in both Slack (mrkdwn code blocks) and
rich tables without dragging in matplotlib for a few charts.
"""

from __future__ import annotations

SPARK = "▁▂▃▄▅▆▇█"


def hbar(value: int, total: int, width: int = 20, filled: str = "█", empty: str = "░") -> str:
    """Horizontal bar. `value/total` mapped to `width` cells. Total=0 returns empty bar."""
    if total <= 0 or width <= 0:
        return ""
    n = round(value / total * width)
    n = max(0, min(width, n))
    return filled * n + empty * (width - n)


def sparkline(values: list[int]) -> str:
    """One-line sparkline (▁▂▃▄▅▆▇█) for time-series data."""
    if not values:
        return ""
    mx = max(values) or 1
    out = []
    for v in values:
        idx = min(int(v / mx * (len(SPARK) - 1)), len(SPARK) - 1)
        out.append(SPARK[idx])
    return "".join(out)


def percent(value: int, total: int) -> str:
    if total <= 0:
        return "  —"
    p = value / total * 100
    return f"{p:>3.0f}%"


# Special-cased brand/term casing that `.capitalize()` would butcher.
_SPECIAL_TERMS = {
    "github": "GitHub",
    "linkedin": "LinkedIn",
    "dou": "DOU",
    "linear": "Linear",
    "slack": "Slack",
    "apple": "Apple",
    "whoop": "WHOOP",
    "strava": "Strava",
    "instagram": "Instagram",
    "goodreads": "Goodreads",
    "vip": "VIP",
    "llm": "LLM",
    "ai": "AI",
}


def format_rule_name(name: str | None) -> str:
    """`linkedin_notifications` → `LinkedIn notifications`. Display only —
    DB keys stay snake_case."""
    if not name:
        return "—"
    parts = name.replace("-", "_").split("_")
    out = []
    for i, p in enumerate(parts):
        low = p.lower()
        if low in _SPECIAL_TERMS:
            out.append(_SPECIAL_TERMS[low])
        elif i == 0:
            out.append(p.capitalize())
        else:
            out.append(p)
    return " ".join(out)


BUCKET_GLYPH = {
    "respond": "🟥",
    "notify": "🟨",
    "ignore": "🟩",
}


SOURCE_LABELS = {
    "header_rule": "Header rule",
    "llm_fast": "LLM (fast)",
    "llm_smart": "LLM (smart)",
    "user_correction": "User correction",
}


def format_source(source: str | None) -> str:
    """Human label for a classifier source — matches the rest of the UI."""
    if not source:
        return "—"
    return SOURCE_LABELS.get(source, source)

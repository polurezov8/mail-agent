"""PII redaction + hydration.

Goal: strip personally identifiable information before sending mail content to
the LLM, then re-hydrate placeholders in the model's reasoning so the user-
facing output still reads naturally.

Scope (v1):
  • emails, URLs, phones, IBANs, credit-card-shaped numbers (regex)
  • personal names from an explicit list (case-insensitive, word-bounded;
    full name AND its ≥3-char parts all collapse to one canonical
    `<PERSON_N>` placeholder)

What we deliberately do NOT redact:
  • The `from_email` / `from_domain` on the email being classified —
    that's the strongest triage signal. If you need maximum privacy,
    also pre-classify based on sender hash and never expose addresses
    (future tier).
  • Names not in the explicit list (NER is out of scope for v1).
"""

from __future__ import annotations

import re

# Order matters: URL first eats `user@host.com` patterns inside URLs.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"https?://[^\s<>]+", re.IGNORECASE)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")),
    # Credit-card-shaped: 13-19 digits, optional groupings of 4 separated by space/dash.
    ("CC", re.compile(r"\b(?:\d{4}[ -]?){3,4}\d{1,4}\b")),
    # Phone: optional country code + 7+ digits with separators. Conservative
    # — kept relatively strict to avoid eating order numbers, message IDs, etc.
    (
        "PHONE",
        re.compile(r"(?<!\w)\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}(?!\w)"),
    ),
]


def _redact_names(s: str, names: list[str]) -> tuple[str, dict[str, str]]:
    """For each full name in `names`, replace the full name + each ≥3-char
    standalone part with a single canonical placeholder. Different people
    get distinct placeholders; same person (full name or any part) gets the
    same placeholder."""
    mapping: dict[str, str] = {}
    if not names or not s:
        return s, mapping
    for i, full_name in enumerate(names, 1):
        full_name = full_name.strip()
        if not full_name:
            continue
        placeholder = f"<PERSON_{i}>"
        # Build all surface forms — full name first, then each ≥3-char token.
        # Longest-first so we don't half-replace inside the full name.
        forms = [full_name]
        forms.extend(p for p in full_name.split() if len(p) >= 3)
        forms = sorted(set(forms), key=len, reverse=True)
        used = False
        for form in forms:
            pattern = re.compile(r"\b" + re.escape(form) + r"\b", re.IGNORECASE)
            if pattern.search(s):
                s = pattern.sub(placeholder, s)
                used = True
        if used:
            mapping[placeholder] = full_name  # canonical form for hydration
    return s, mapping


def redact_text(s: str, names: list[str] | None = None) -> tuple[str, dict[str, str]]:
    """Replace PII spans with `<KIND_N>` placeholders. Same value across the
    text gets the same placeholder (stable de-anonymization).

    `names`: optional list of canonical full names to redact. Each name and
    its individual parts (≥3 chars) collapse to one `<PERSON_N>` placeholder."""
    if not s:
        return s, {}
    mapping: dict[str, str] = {}

    # 1. PII patterns first so name regex can't match inside emails/URLs.
    counters: dict[str, int] = {}
    value_to_placeholder: dict[tuple[str, str], str] = {}

    for kind, pattern in PATTERNS:

        def _replace(match: re.Match[str], kind=kind) -> str:
            original = match.group(0)
            key = (kind, original)
            if key in value_to_placeholder:
                return value_to_placeholder[key]
            counters[kind] = counters.get(kind, 0) + 1
            ph = f"<{kind}_{counters[kind]}>"
            value_to_placeholder[key] = ph
            mapping[ph] = original
            return ph

        s = pattern.sub(_replace, s)

    # 2. Names last — operates on text where PII spans are already placeholders.
    if names:
        s, name_map = _redact_names(s, names)
        mapping.update(name_map)
    return s, mapping


def hydrate(s: str, mapping: dict[str, str]) -> str:
    """Reverse `redact_text` substitutions in the given string."""
    if not mapping or not s:
        return s
    # Replace longer placeholders first to avoid `<EMAIL_1>` matching inside `<EMAIL_10>`.
    for ph in sorted(mapping, key=len, reverse=True):
        s = s.replace(ph, mapping[ph])
    return s

from __future__ import annotations

from ..config import Rule, RuleMatch
from ..models import EmailMessage, RuleName, TriageDecision


def _match(email: EmailMessage, m: RuleMatch) -> bool:
    if m.from_domain and email.from_domain not in {d.lower() for d in m.from_domain}:
        return False
    if m.from_email and email.from_email.lower() not in {e.lower() for e in m.from_email}:
        return False
    if m.subject_contains:
        subject_lower = email.subject.lower()
        if not any(needle.lower() in subject_lower for needle in m.subject_contains):
            return False
    if m.has_header and not all(h in email.headers for h in m.has_header):
        return False
    if m.headers_contains:
        for header, needle in m.headers_contains.items():
            value = email.headers.get(header, "")
            if needle.lower() not in value.lower():
                return False
    return True


def match_first(email: EmailMessage, rules: list[Rule]) -> TriageDecision | None:
    for rule in rules:
        if _match(email, rule.match):
            return TriageDecision(
                bucket=rule.bucket,
                rule_name=RuleName(rule.name),
                reasoning=f"Matched header rule '{rule.name}': {rule.description}",
                confidence=1.0,
                source="header_rule",
            )
    return None

"""Mutate rules.yaml safely: read → modify → write."""

from __future__ import annotations

from pathlib import Path

import yaml


def add_nl_rule(rule_text: str, path: Path = Path("config/rules.yaml")) -> tuple[bool, int]:
    """Append a natural-language rule to llm.nl_rules.

    Returns (added, total_count). `added` is False when the rule is an exact
    duplicate of an existing one (no write performed).
    """
    rule_text = rule_text.strip()
    if not rule_text:
        raise ValueError("Empty rule text")
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    llm = raw.setdefault("llm", {})
    nl_rules = llm.setdefault("nl_rules", []) or []
    if rule_text in nl_rules:
        return False, len(nl_rules)
    nl_rules.append(rule_text)
    llm["nl_rules"] = nl_rules
    raw["llm"] = llm
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return True, len(nl_rules)


def remove_nl_rule(index: int, path: Path = Path("config/rules.yaml")) -> tuple[bool, str | None]:
    """Remove an NL rule by 1-based index. Returns (removed, removed_text)."""
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    nl_rules = raw.get("llm", {}).get("nl_rules") or []
    if index < 1 or index > len(nl_rules):
        return False, None
    removed = nl_rules.pop(index - 1)
    raw.setdefault("llm", {})["nl_rules"] = nl_rules
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return True, removed

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mail_agent.rules.edit import add_nl_rule, remove_nl_rule


def _seed_yaml(tmp_path: Path, nl_rules: list[str] | None = None) -> Path:
    raw = {
        "rules": [],
        "llm": {"enabled": True, "nl_rules": nl_rules or []},
    }
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_add_nl_rule_appends(tmp_path):
    path = _seed_yaml(tmp_path)
    added, total = add_nl_rule("new rule", path)
    assert added is True
    assert total == 1
    parsed = yaml.safe_load(path.read_text())
    assert parsed["llm"]["nl_rules"] == ["new rule"]


def test_add_nl_rule_skips_duplicate(tmp_path):
    path = _seed_yaml(tmp_path, ["existing rule"])
    added, total = add_nl_rule("existing rule", path)
    assert added is False
    assert total == 1


def test_add_nl_rule_rejects_empty(tmp_path):
    path = _seed_yaml(tmp_path)
    with pytest.raises(ValueError):
        add_nl_rule("   ", path)


def test_add_nl_rule_creates_missing_llm_section(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"rules": []}))
    added, _ = add_nl_rule("a rule", path)
    assert added is True


def test_remove_nl_rule_by_index(tmp_path):
    path = _seed_yaml(tmp_path, ["a", "b", "c"])
    removed, text = remove_nl_rule(2, path)
    assert removed is True
    assert text == "b"
    parsed = yaml.safe_load(path.read_text())
    assert parsed["llm"]["nl_rules"] == ["a", "c"]


def test_remove_nl_rule_out_of_range(tmp_path):
    path = _seed_yaml(tmp_path, ["a"])
    removed, text = remove_nl_rule(99, path)
    assert removed is False
    assert text is None

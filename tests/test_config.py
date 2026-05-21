from __future__ import annotations

import yaml

from mail_agent.config import load_config
from mail_agent.models import Bucket


def test_load_config_parses_rules_and_llm(tmp_path):
    raw = {
        "rules": [
            {
                "name": "github",
                "description": "GH",
                "match": {"from_domain": ["github.com"]},
                "bucket": "ignore",
                "auto_mark_read": True,
            }
        ],
        "llm": {
            "enabled": True,
            "model_fast": "claude-haiku-test",
            "model_smart": "claude-sonnet-test",
            "confidence_threshold": 0.7,
            "auto_mark_min_confidence": 0.85,
            "nl_rules": ["test rule"],
        },
    }
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw))

    cfg = load_config(path)
    assert len(cfg.rules) == 1
    assert cfg.rules[0].bucket == Bucket.IGNORE
    assert cfg.rules[0].auto_mark_read is True
    assert cfg.llm.auto_mark_min_confidence == 0.85
    assert cfg.llm.nl_rules == ["test rule"]


def test_load_config_defaults_when_optional_fields_missing(tmp_path):
    raw = {
        "rules": [
            {
                "name": "x",
                "description": "x",
                "match": {},
                "bucket": "notify",
            }
        ],
        "llm": {},
    }
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw))

    cfg = load_config(path)
    assert cfg.rules[0].auto_mark_read is False
    assert cfg.llm.enabled is True
    assert cfg.llm.nl_rules == []

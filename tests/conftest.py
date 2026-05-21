"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mail_agent.config import LLMConfig, Rule, RuleMatch
from mail_agent.models import (
    AccountName,
    Bucket,
    EmailMessage,
    MessageId,
    RuleName,
    ThreadId,
    TriageDecision,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point GMAIL_DB_PATH at a per-test sqlite file. Returns the path."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GMAIL_DB_PATH", str(db_path))
    return db_path


@pytest.fixture
def sample_email():
    def _build(**overrides) -> EmailMessage:
        base = dict(
            id=MessageId("m-test"),
            thread_id=ThreadId("t-test"),
            account=AccountName("personal"),
            from_email="user@example.com",
            from_name="User",
            to=["me@example.com"],
            subject="Hello",
            snippet="Hi",
            headers={},
            received_at=datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc),
        )
        base.update(overrides)
        return EmailMessage(**base)

    return _build


@pytest.fixture
def sample_rules():
    return [
        Rule(
            name="github",
            description="GitHub notifications",
            match=RuleMatch(from_domain=["github.com"]),
            bucket=Bucket.IGNORE,
            auto_mark_read=True,
        ),
        Rule(
            name="newsletters",
            description="List-Unsubscribe newsletters",
            match=RuleMatch(has_header=["List-Unsubscribe"]),
            bucket=Bucket.IGNORE,
            auto_mark_read=True,
        ),
        Rule(
            name="linear",
            description="Linear notifications",
            match=RuleMatch(from_domain=["linear.app"]),
            bucket=Bucket.NOTIFY,
            auto_mark_read=False,
        ),
    ]


@pytest.fixture
def llm_config():
    return LLMConfig(
        enabled=True,
        model_fast="claude-haiku-test",
        model_smart="claude-sonnet-test",
        confidence_threshold=0.7,
        auto_mark_min_confidence=0.85,
        nl_rules=["Calendar invites → ignore unless cancellation"],
    )


@pytest.fixture
def decision_factory():
    def _build(**overrides) -> TriageDecision:
        base = dict(
            bucket=Bucket.IGNORE,
            rule_name=RuleName("github"),
            reasoning="test",
            confidence=1.0,
            source="header_rule",
            model=None,
            auto_mark=True,
        )
        base.update(overrides)
        return TriageDecision(**base)

    return _build

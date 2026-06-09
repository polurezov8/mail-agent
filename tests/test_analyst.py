"""Tests for mail_agent.analyst."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock


from mail_agent.analyst import (
    AnalysisAnswer,
    AnalysisPlan,
    ExtractionResult,
    analyse_inbox,
    build_analysis_plan,
    extract_batch,
    synthesise,
)


def _dt(iso: str = "2026-04-01T00:00:00+00:00") -> datetime:
    return datetime.fromisoformat(iso)


# ── build_analysis_plan ────────────────────────────────────────────────────────

def test_build_analysis_plan_returns_valid_plan(llm_config, monkeypatch):
    expected = AnalysisPlan(
        gmail_query="from:apple.com newer_than:3m",
        extraction_instruction="extract: price, date, item",
        synthesis_instruction="sum prices by month",
    )

    class _FakeStructured:
        def invoke(self, messages):
            return expected

    class _FakeModel:
        def with_structured_output(self, _schema):
            return _FakeStructured()

    monkeypatch.setattr("mail_agent.analyst.ChatAnthropic", lambda **kw: _FakeModel())

    plan = build_analysis_plan("Apple subscriptions last 3 months", llm_config)
    assert plan.gmail_query == "from:apple.com newer_than:3m"
    assert plan.extraction_instruction != ""
    assert plan.synthesis_instruction != ""


# ── extract_batch ──────────────────────────────────────────────────────────────

def test_extract_batch_returns_structured_data(llm_config, monkeypatch):
    raw_output = json.dumps([
        {"message_id": "m1", "extracted": {"amount": 9.99, "item": "iCloud+"}},
        {"message_id": "m2", "extracted": None},
    ])

    class _FakeModel:
        def invoke(self, messages):
            r = MagicMock()
            r.content = raw_output
            return r

    monkeypatch.setattr("mail_agent.analyst.ChatAnthropic", lambda **kw: _FakeModel())

    emails = [
        {"message_id": "m1", "from_email": "noreply@apple.com", "subject": "Receipt", "body": "iCloud+ $9.99"},
        {"message_id": "m2", "from_email": "promo@apple.com", "subject": "Sale", "body": "Big sale!"},
    ]
    results = extract_batch(emails, "extract: price, item", llm_config)
    assert len(results) == 2
    assert results[0]["extracted"]["amount"] == 9.99
    assert results[1]["extracted"] is None


def test_extract_batch_handles_json_parse_failure(llm_config, monkeypatch):
    class _FakeModel:
        def invoke(self, messages):
            r = MagicMock()
            r.content = "not valid json {{{"
            return r

    monkeypatch.setattr("mail_agent.analyst.ChatAnthropic", lambda **kw: _FakeModel())

    results = extract_batch([{"message_id": "m1", "body": "text"}], "any instruction", llm_config)
    assert results == []


# ── synthesise ────────────────────────────────────────────────────────────────

def test_synthesise_returns_analysis_answer(llm_config, monkeypatch):
    expected = AnalysisAnswer(
        answer="Total: $29.97 across 3 months",
        format_hint="number",
        source_count=3,
    )

    class _FakeStructured:
        def invoke(self, messages):
            return expected

    class _FakeModel:
        def with_structured_output(self, _schema):
            return _FakeStructured()

    monkeypatch.setattr("mail_agent.analyst.ChatAnthropic", lambda **kw: _FakeModel())

    results = [
        ExtractionResult(
            message_id="m1",
            from_email="noreply@apple.com",
            subject="Receipt",
            received_at=_dt(),
            extracted={"amount": 9.99},
        )
    ]
    answer = synthesise(results, "sum prices", "total Apple charges", llm_config)
    assert answer.answer == "Total: $29.97 across 3 months"
    assert answer.format_hint in ("table", "bullet_list", "paragraph", "number")


def test_synthesise_returns_fallback_on_truncated_output(llm_config, monkeypatch):
    """LLM output truncated at max_tokens → parser raises ValidationError on empty
    tool args. synthesise must return a clean fallback, not leak the Pydantic error."""

    class _FakeStructured:
        def invoke(self, messages):
            AnalysisAnswer(**{})  # reproduces the empty-args ValidationError

    class _FakeModel:
        def with_structured_output(self, _schema):
            return _FakeStructured()

    monkeypatch.setattr("mail_agent.analyst.ChatAnthropic", lambda **kw: _FakeModel())

    results = [
        ExtractionResult(
            message_id="m1",
            from_email="noreply@apple.com",
            subject="Receipt",
            received_at=_dt(),
            extracted={"amount": 9.99},
        ),
        ExtractionResult(
            message_id="m2",
            from_email="noreply@apple.com",
            subject="Receipt 2",
            received_at=_dt(),
            extracted={"amount": 4.99},
        ),
    ]
    answer = synthesise(results, "sum prices", "total Apple charges", llm_config)
    assert isinstance(answer, AnalysisAnswer)
    assert answer.format_hint == "paragraph"
    assert answer.source_count == 2  # relevant emails are known even when synthesis fails
    assert "too long" in answer.answer.lower() or "couldn't" in answer.answer.lower()


# ── analyse_inbox (orchestrator) ───────────────────────────────────────────────

def test_analyse_inbox_end_to_end(llm_config, monkeypatch):
    """Full pipeline with all external calls mocked."""
    from mail_agent.models import AccountName, MessageId, ThreadId
    from datetime import datetime, timezone

    plan = AnalysisPlan(
        gmail_query="from:apple.com newer_than:3m",
        extraction_instruction="extract price",
        synthesis_instruction="sum prices",
    )
    answer = AnalysisAnswer(answer="$29.97", format_hint="number", source_count=1)

    # Minimal EmailMessage-like namedtuple substitute
    class _Msg:
        def __init__(self):
            self.id = MessageId("m1")
            self.thread_id = ThreadId("t1")
            self.account = AccountName("personal")
            self.from_email = "noreply@apple.com"
            self.subject = "Receipt"
            self.snippet = "iCloud+ $9.99"
            self.received_at = datetime(2026, 4, 1, tzinfo=timezone.utc)

    class _Acct:
        name = "personal"
        is_authorized = True

    monkeypatch.setattr("mail_agent.analyst.build_analysis_plan", lambda q, cfg: plan)
    monkeypatch.setattr("mail_agent.analyst.synthesise", lambda *a, **kw: answer)
    monkeypatch.setattr("mail_agent.gmail.accounts.load_accounts", lambda: [_Acct()])
    monkeypatch.setattr("mail_agent.gmail.client.search_messages", lambda acct, q, limit: [_Msg()])
    monkeypatch.setattr("mail_agent.gmail.client.fetch_message_body", lambda acct, mid: "iCloud+ $9.99")
    monkeypatch.setattr(
        "mail_agent.analyst.extract_batch",
        lambda emails, instr, cfg: [{"message_id": "m1", "extracted": {"amount": 9.99}}],
    )

    result = analyse_inbox("Apple subscriptions last 3 months", llm_config)
    assert result.answer == "$29.97"
    assert result.source_count == 1


def test_analyse_inbox_skips_failed_body_fetch(llm_config, monkeypatch):
    """fetch_message_body raises for one message — pipeline continues with snippet fallback."""
    from mail_agent.models import AccountName, MessageId, ThreadId

    plan = AnalysisPlan(
        gmail_query="from:apple.com",
        extraction_instruction="extract price",
        synthesis_instruction="sum prices",
    )
    answer = AnalysisAnswer(answer="$9.99", format_hint="number", source_count=1)

    class _Msg:
        def __init__(self, mid):
            self.id = MessageId(mid)
            self.thread_id = ThreadId("t1")
            self.account = AccountName("personal")
            self.from_email = "noreply@apple.com"
            self.subject = "Receipt"
            self.snippet = "fallback snippet"
            self.received_at = datetime(2026, 4, 1, tzinfo=timezone.utc)

    class _Acct:
        name = "personal"
        is_authorized = True

    fetch_calls: list[str] = []

    def _failing_fetch(acct, mid):
        fetch_calls.append(mid)
        if mid == "m2":
            raise RuntimeError("API error")
        return "body text"

    monkeypatch.setattr("mail_agent.analyst.build_analysis_plan", lambda q, cfg: plan)
    monkeypatch.setattr("mail_agent.analyst.synthesise", lambda *a, **kw: answer)
    monkeypatch.setattr("mail_agent.gmail.accounts.load_accounts", lambda: [_Acct()])
    monkeypatch.setattr(
        "mail_agent.gmail.client.search_messages",
        lambda acct, q, limit: [_Msg("m1"), _Msg("m2")],
    )
    monkeypatch.setattr("mail_agent.gmail.client.fetch_message_body", _failing_fetch)
    monkeypatch.setattr(
        "mail_agent.analyst.extract_batch",
        lambda emails, instr, cfg: [{"message_id": e["message_id"], "extracted": {"amount": 9.99}} for e in emails],
    )

    result = analyse_inbox("Apple charges", llm_config)
    # m2 failed body fetch but pipeline completed — both messages still in batch
    assert "m2" in fetch_calls
    assert result.answer == "$9.99"


def test_analyse_inbox_returns_no_match_message(llm_config, monkeypatch):
    """No emails found → returns friendly no-match answer without calling LLM."""
    plan = AnalysisPlan(
        gmail_query="from:nowhere.example.com",
        extraction_instruction="x",
        synthesis_instruction="x",
    )

    class _Acct:
        name = "personal"
        is_authorized = True

    monkeypatch.setattr("mail_agent.analyst.build_analysis_plan", lambda q, cfg: plan)
    monkeypatch.setattr("mail_agent.gmail.accounts.load_accounts", lambda: [_Acct()])
    monkeypatch.setattr("mail_agent.gmail.client.search_messages", lambda *a, **kw: [])

    result = analyse_inbox("invoices from nobody", llm_config)
    assert result.source_count == 0
    assert "No emails" in result.answer

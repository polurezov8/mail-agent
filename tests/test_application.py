"""Tests for the application use case seam."""

from __future__ import annotations

from mail_agent import application
from mail_agent.application import SearchResult, TriageRunResult
from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    MessageId,
    SurfaceResult,
    TriageDecision,
)
from mail_agent.search import SearchPlan


def _surface(email, bucket: Bucket = Bucket.NOTIFY) -> SurfaceResult:
    return SurfaceResult(
        email=email,
        decision=TriageDecision(
            bucket=bucket,
            rule_name=None,
            reasoning="t",
            confidence=1.0,
            source="header_rule",
            auto_mark=False,
        ),
    )


def _auto(email) -> AutoMarkResult:
    return AutoMarkResult(
        email=email,
        decision=TriageDecision(
            bucket=Bucket.IGNORE,
            rule_name="github",
            reasoning="t",
            confidence=1.0,
            source="header_rule",
            auto_mark=True,
        ),
    )


# ---------------------------------------------------------------- #
# TriageRunResult — gated counts derive correctly from result list.
# ---------------------------------------------------------------- #


def test_triage_run_result_counts(sample_email):
    e1 = sample_email(id=MessageId("a"))
    e2 = sample_email(id=MessageId("b"))
    e3 = sample_email(id=MessageId("c"))
    result = TriageRunResult(
        results=[_auto(e1), _surface(e2), _surface(e3, bucket=Bucket.RESPOND)],
        slack_summary=None,
    )
    assert result.n_total == 3
    assert result.n_auto_marked == 1
    assert result.n_surfaced == 2


def test_triage_run_result_empty():
    result = TriageRunResult(results=[], slack_summary=None)
    assert result.n_total == 0
    assert result.n_auto_marked == 0
    assert result.n_surfaced == 0


# ---------------------------------------------------------------- #
# application.stats / brief are typed delegates.
# ---------------------------------------------------------------- #


def test_application_stats_returns_metrics_snapshot(isolated_db, monkeypatch):
    captured = {}

    def fake_compute_metrics(*, period, accounts):
        captured["period"] = period
        captured["accounts"] = accounts
        from mail_agent.store.stats import MetricsSnapshot

        return MetricsSnapshot(
            period=period,
            since=None,
            total_processed=0,
            bucket_counts={},
            source_counts={},
            rule_top=[],
            marked_read=0,
            corrections=0,
            uncertain_band=0,
            daily=[],
            top_senders_auto_marked=[],
        )

    monkeypatch.setattr("mail_agent.application.compute_metrics", fake_compute_metrics)
    snap = application.stats(period="day", accounts=["work"])
    assert snap.period == "day"
    assert captured == {"period": "day", "accounts": ["work"]}


def test_application_brief_returns_brief_summary(monkeypatch):
    captured = {}

    def fake_build_brief(*, hours, accounts):
        captured["hours"] = hours
        captured["accounts"] = accounts
        from mail_agent.brief import BriefSummary

        return BriefSummary(
            since="2026-05-22T00:00:00+00:00",
            hours=hours,
            processed_total=0,
            bucket_counts={},
            rule_counts=[],
            marked_read=0,
            notable_respond=[],
            notable_notify=[],
            uncertain_band=0,
            corrections_recent=0,
        )

    monkeypatch.setattr("mail_agent.application.build_brief", fake_build_brief)
    summary = application.brief(hours=12, accounts=["personal"])
    assert summary.hours == 12
    assert captured == {"hours": 12, "accounts": ["personal"]}


# ---------------------------------------------------------------- #
# application.search composes plan + hits + filters accounts.
# ---------------------------------------------------------------- #


def test_application_search_uses_plan_limit(monkeypatch, sample_email):
    """plan.suggested_limit overrides the caller's limit (matches CLI behavior)."""
    plan = SearchPlan(gmail_query="from:foo", reasoning="test", suggested_limit=5)
    monkeypatch.setattr(
        "mail_agent.application.build_gmail_query", lambda q, llm: plan
    )

    class _FakeAccount:
        name = "personal"
        is_authorized = True

    monkeypatch.setattr(
        "mail_agent.gmail.accounts.load_accounts", lambda: [_FakeAccount()]
    )
    captured_limit = {}

    def _fake_search(acct, q, *, limit):
        captured_limit["v"] = limit
        return [sample_email(id=MessageId(f"h{i}")) for i in range(20)]

    monkeypatch.setattr("mail_agent.gmail.client.search_messages", _fake_search)

    # cli passes limit=10, but plan.suggested_limit=5 wins.
    from mail_agent.config import Config, LLMConfig

    cfg = Config(rules=[], llm=LLMConfig(enabled=True))
    result: SearchResult = application.search("anything", cfg, limit=10)

    assert captured_limit["v"] == 5
    assert len(result.hits) == 5
    assert result.plan is plan


def test_application_search_account_filter(monkeypatch, sample_email):
    plan = SearchPlan(gmail_query="x", reasoning="", suggested_limit=None)
    monkeypatch.setattr(
        "mail_agent.application.build_gmail_query", lambda q, llm: plan
    )

    class _Acct:
        def __init__(self, name):
            self.name = name
            self.is_authorized = True

    accounts_seen: list[str] = []

    def _fake_search(acct, q, *, limit):
        accounts_seen.append(acct.name)
        return [sample_email(id=MessageId(f"{acct.name}-1"))]

    monkeypatch.setattr(
        "mail_agent.gmail.accounts.load_accounts",
        lambda: [_Acct("personal"), _Acct("work")],
    )
    monkeypatch.setattr("mail_agent.gmail.client.search_messages", _fake_search)

    from mail_agent.config import Config, LLMConfig

    cfg = Config(rules=[], llm=LLMConfig(enabled=True))
    application.search("q", cfg, accounts=["work"])
    assert accounts_seen == ["work"]

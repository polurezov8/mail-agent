"""Direct unit tests for decide_for_email — no graph, no Gmail, no LLM."""

from __future__ import annotations


from mail_agent.config import Config
from mail_agent.graph.triage_decision import decide_for_email
from mail_agent.models import Bucket, MessageId, TriageDecision


def _cfg(rules, *, llm_enabled: bool, llm_config) -> Config:
    return Config(rules=rules, llm=llm_config)


def _no_override(_email: str) -> None:
    return None


def _no_thread_context(_email):
    return None


# ---------------------------------------------------------------- #
# Stage 1: user correction wins.
# ---------------------------------------------------------------- #


def test_override_wins_over_everything(sample_rules, llm_config, sample_email):
    cfg = _cfg(sample_rules, llm_enabled=True, llm_config=llm_config)
    email = sample_email(from_email="boss@example.com")
    decision = decide_for_email(
        email,
        cfg,
        get_override=lambda s: "respond",
        get_thread_context=_no_thread_context,
    )
    assert decision.bucket == Bucket.RESPOND
    assert decision.source == "user_correction"
    assert decision.confidence == 1.0
    # An override to non-IGNORE leaves auto_mark off.
    assert decision.auto_mark is False


def test_override_to_ignore_sets_auto_mark(sample_rules, llm_config, sample_email):
    cfg = _cfg(sample_rules, llm_enabled=True, llm_config=llm_config)
    email = sample_email(from_email="newsletter@example.com")
    decision = decide_for_email(
        email,
        cfg,
        get_override=lambda s: "ignore",
        get_thread_context=_no_thread_context,
    )
    assert decision.bucket == Bucket.IGNORE
    assert decision.auto_mark is True


# ---------------------------------------------------------------- #
# Stage 2: header rule match short-circuits LLM.
# ---------------------------------------------------------------- #


def test_header_rule_match_skips_llm(sample_rules, llm_config, sample_email, monkeypatch):
    """Email matches a rule → returned decision is the rule's decision; LLM is not invoked."""
    cfg = _cfg(sample_rules, llm_enabled=True, llm_config=llm_config)
    email = sample_email(
        id=MessageId("gh-1"),
        from_email="notifications@github.com",
        headers={},
    )

    classify_called = {"n": 0}

    def fake_classify(*args, **kwargs):
        classify_called["n"] += 1
        raise AssertionError("classify must not be called when a rule matches")

    monkeypatch.setattr("mail_agent.graph.triage_decision.classify", fake_classify)

    decision = decide_for_email(
        email,
        cfg,
        get_override=_no_override,
        get_thread_context=_no_thread_context,
    )
    assert decision.bucket == Bucket.IGNORE
    assert decision.rule_name == "github"
    assert classify_called["n"] == 0


# ---------------------------------------------------------------- #
# Stage 3: LLM fallback when no rule matches.
# ---------------------------------------------------------------- #


def test_llm_called_when_no_rule_matches(sample_rules, llm_config, sample_email, monkeypatch):
    cfg = _cfg(sample_rules, llm_enabled=True, llm_config=llm_config)
    email = sample_email(from_email="stranger@example.org")  # no rule matches

    captured_thread_context = {"value": "unset"}

    def fake_classify(_email, _rules, _llm_cfg, thread_context):
        captured_thread_context["value"] = thread_context
        return TriageDecision(
            bucket=Bucket.NOTIFY,
            rule_name=None,
            reasoning="llm-said-so",
            confidence=0.7,
            source="llm_fast",
            model="claude-haiku-test",
        )

    monkeypatch.setattr("mail_agent.graph.triage_decision.classify", fake_classify)

    decision = decide_for_email(
        email,
        cfg,
        get_override=_no_override,
        get_thread_context=lambda e: ["prior-thread-msg"],
    )
    assert decision.source == "llm_fast"
    assert captured_thread_context["value"] == ["prior-thread-msg"]


# ---------------------------------------------------------------- #
# Stage 4: LLM disabled + no rule → notify fallback.
# ---------------------------------------------------------------- #


def test_llm_disabled_falls_back_to_notify(sample_rules, llm_config, sample_email, monkeypatch):
    disabled = llm_config.model_copy(update={"enabled": False})
    cfg = _cfg(sample_rules, llm_enabled=False, llm_config=disabled)
    email = sample_email(from_email="random@example.com")

    def boom(*args, **kwargs):
        raise AssertionError("classify must not be called when LLM disabled")

    monkeypatch.setattr("mail_agent.graph.triage_decision.classify", boom)

    decision = decide_for_email(
        email,
        cfg,
        get_override=_no_override,
        get_thread_context=_no_thread_context,
    )
    assert decision.bucket == Bucket.NOTIFY
    assert decision.confidence == 0.0
    assert decision.rule_name is None

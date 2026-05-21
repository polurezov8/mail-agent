from __future__ import annotations

from mail_agent.search import SearchPlan, build_gmail_query


def test_empty_query_short_circuits(llm_config):
    plan = build_gmail_query("   ", llm_config)
    assert plan.gmail_query == ""


def test_build_gmail_query_uses_llm(llm_config, monkeypatch):
    """LLM call is mocked — verify wiring (prompt + structured output)."""

    captured = {}

    class _FakeStructured:
        def invoke(self, messages):
            captured["messages"] = messages
            return SearchPlan(
                gmail_query="from:jane newer_than:7d",
                reasoning="sender + 7d window",
            )

    class _FakeModel:
        def with_structured_output(self, _schema):
            return _FakeStructured()

    monkeypatch.setattr(
        "mail_agent.search.ChatAnthropic", lambda **kw: _FakeModel()
    )

    plan = build_gmail_query("anything from Jane last week", llm_config)
    assert plan.gmail_query == "from:jane newer_than:7d"
    assert "Jane" in captured["messages"][1]["content"]
    # System prompt mentions Gmail operators
    assert "newer_than" in captured["messages"][0]["content"]

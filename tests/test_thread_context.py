from __future__ import annotations

from datetime import datetime

from mail_agent.llm.prompts import render_thread_context, user_prompt
from mail_agent.models import ThreadMessage


def _msg(received_iso: str, **overrides) -> ThreadMessage:
    base = dict(
        from_email="prior@example.com",
        subject="prior subject",
        snippet="prior snippet",
        received_at=datetime.fromisoformat(received_iso),
    )
    base.update(overrides)
    return ThreadMessage(**base)


def test_render_thread_context_empty_returns_empty():
    assert render_thread_context([]) == ""


def test_render_thread_context_emits_prior_message_blocks():
    msgs = [
        _msg("2026-05-20T08:00:00+00:00", from_email="alice@x.com"),
        _msg("2026-05-20T10:00:00+00:00", from_email="bob@y.com"),
    ]
    out = render_thread_context(msgs)
    assert "<thread_history" in out
    assert "alice@x.com" in out
    assert "bob@y.com" in out
    assert out.count("<prior_message>") == 2


def test_user_prompt_includes_thread_context(sample_email):
    email = sample_email()
    thread_ctx = [_msg("2026-05-20T08:00:00+00:00")]
    prompt = user_prompt(email, [], nl_rules=[], thread_context=thread_ctx)
    assert "thread_history" in prompt
    assert "prior snippet" in prompt


def test_user_prompt_skips_thread_block_when_none(sample_email):
    email = sample_email()
    prompt = user_prompt(email, [], nl_rules=[], thread_context=None)
    assert "thread_history" not in prompt

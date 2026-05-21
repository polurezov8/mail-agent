from __future__ import annotations

import os
from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from ..config import LLMConfig, Rule
from ..models import EmailMessage, ThreadMessage, TriageDecision
from ..redact import hydrate, redact_text
from .prompts import SYSTEM, user_prompt


@lru_cache(maxsize=2)
def _model(model_id: str) -> ChatAnthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return ChatAnthropic(model=model_id, temperature=0, api_key=api_key)


def classify(
    email: EmailMessage,
    rules: list[Rule],
    cfg: LLMConfig,
    thread_context: list[ThreadMessage] | None = None,
) -> TriageDecision:
    # Build prompt — optionally with PII (regex) + named persons stripped from
    # body + thread snippets. Either flag triggers redaction.
    needs_redact = cfg.redact_pii or bool(cfg.redact_names)
    names = cfg.redact_names if cfg.redact_names else None
    if needs_redact:
        red_snippet, snippet_map = redact_text(email.snippet, names=names)
        red_subject, subject_map = redact_text(email.subject, names=names)
        red_email = email.model_copy(update={"snippet": red_snippet, "subject": red_subject})
        mapping = {**snippet_map, **subject_map}
        red_thread: list[ThreadMessage] | None = None
        if thread_context:
            red_thread = []
            for m in thread_context:
                m_red, m_map = redact_text(m.snippet, names=names)
                s_red, s_map = redact_text(m.subject, names=names)
                red_thread.append(m.model_copy(update={"snippet": m_red, "subject": s_red}))
                mapping.update(m_map)
                mapping.update(s_map)
        prompt = user_prompt(red_email, rules, cfg.nl_rules, red_thread)
    else:
        prompt = user_prompt(email, rules, cfg.nl_rules, thread_context)
        mapping = {}

    fast = _model(cfg.model_fast).with_structured_output(TriageDecision)
    decision: TriageDecision = fast.invoke(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ]
    )
    decision.source = "llm_fast"
    decision.model = cfg.model_fast

    if decision.confidence >= cfg.confidence_threshold:
        if mapping and decision.reasoning:
            decision.reasoning = hydrate(decision.reasoning, mapping)
        return decision

    smart = _model(cfg.model_smart).with_structured_output(TriageDecision)
    escalated: TriageDecision = smart.invoke(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ]
    )
    escalated.source = "llm_smart"
    escalated.model = cfg.model_smart
    if mapping and escalated.reasoning:
        escalated.reasoning = hydrate(escalated.reasoning, mapping)
    return escalated

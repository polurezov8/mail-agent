"""Per-email triage decision. Names the override → rule → LLM → fallback chain.

node_triage was inlining this sequence inside a loop closure over GraphState.
The sequence has 4 branches with distinct semantics; extracting it gives the
business rule a name, its own test surface, and isolates it from graph state
plumbing.

DB and Gmail dependencies are injected as callables so callers (graph node,
tests, future replay tools) decide whether to hit real backends or stubs.
"""

from __future__ import annotations

from typing import Callable

from ..llm.classifier import classify
from ..models import Bucket, EmailMessage, TriageDecision
from ..rules.header import match_first

OverrideLookup = Callable[[str], str | None]
ThreadContextLookup = Callable[[EmailMessage], object | None]


def decide_for_email(
    email: EmailMessage,
    cfg,
    *,
    get_override: OverrideLookup,
    get_thread_context: ThreadContextLookup,
) -> TriageDecision:
    """Resolve a triage decision for one email.

    Stages, in order:
      1. Sender-level user correction (highest priority — explicit human override)
      2. Header rule (deterministic match against config/rules.yaml)
      3. LLM classifier (only if cfg.llm.enabled)
      4. Static notify fallback (LLM disabled and no rule matched)
    """
    override = get_override(email.from_email)
    if override is not None:
        return _override_decision(override)

    decision = match_first(email, cfg.rules)
    if decision is not None:
        return decision

    if not cfg.llm.enabled:
        return _llm_disabled_fallback()

    thread_context = get_thread_context(email)
    return classify(email, cfg.rules, cfg.llm, thread_context)


def _override_decision(override_value: str) -> TriageDecision:
    override_bucket = Bucket(override_value)
    return TriageDecision(
        bucket=override_bucket,
        rule_name=None,
        reasoning=f"Sender-level user correction → {override_value}.",
        confidence=1.0,
        source="user_correction",
        auto_mark=override_bucket == Bucket.IGNORE,
    )


def _llm_disabled_fallback() -> TriageDecision:
    return TriageDecision(
        bucket=Bucket.NOTIFY,
        rule_name=None,
        reasoning="No rule matched; LLM disabled. Defaulting to notify.",
        confidence=0.0,
        source="header_rule",
    )

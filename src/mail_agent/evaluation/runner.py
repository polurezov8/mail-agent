from __future__ import annotations

from ..config import Config
from ..llm.classifier import classify
from ..models import Bucket, TriageDecision
from ..rules.header import match_first
from .dataset import EvalReport, ExampleResult, LabeledExample


def _classify(cfg: Config, example: LabeledExample) -> TriageDecision:
    decision = match_first(example.email, cfg.rules)
    if decision is None:
        if cfg.llm.enabled:
            # Eval doesn't fetch thread history — fixtures stand alone.
            return classify(example.email, cfg.rules, cfg.llm, thread_context=None)
        return TriageDecision(
            bucket=Bucket.NOTIFY,
            rule_name=None,
            reasoning="No rule matched; LLM disabled.",
            confidence=0.0,
            source="header_rule",
        )
    return decision


def evaluate(cfg: Config, examples: list[LabeledExample]) -> EvalReport:
    results = [ExampleResult.from_decision(ex, _classify(cfg, ex)) for ex in examples]
    return EvalReport(results=results)

from __future__ import annotations

from mail_agent.graph.build import _gate
from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    SurfaceResult,
)


def test_gate_promotes_to_auto_mark(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.95, auto_mark=True)
    result = _gate(sample_email(), decision, min_confidence=0.85)
    assert isinstance(result, AutoMarkResult)


def test_gate_blocks_when_decision_does_not_opt_in(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.99, auto_mark=False)
    result = _gate(sample_email(), decision, min_confidence=0.85)
    assert isinstance(result, SurfaceResult)


def test_gate_blocks_when_below_confidence_floor(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.8, auto_mark=True)
    result = _gate(sample_email(), decision, min_confidence=0.85)
    assert isinstance(result, SurfaceResult)


def test_gate_blocks_when_bucket_is_not_ignore(sample_email, decision_factory):
    for bucket in (Bucket.RESPOND, Bucket.NOTIFY):
        decision = decision_factory(bucket=bucket, confidence=0.99, auto_mark=True)
        result = _gate(sample_email(), decision, min_confidence=0.85)
        assert isinstance(result, SurfaceResult)


def test_gate_blocks_when_rule_name_is_none_and_no_opt_in(sample_email, decision_factory):
    decision = decision_factory(
        bucket=Bucket.IGNORE, confidence=0.99, rule_name=None, auto_mark=False
    )
    result = _gate(sample_email(), decision, min_confidence=0.85)
    assert isinstance(result, SurfaceResult)


def test_gate_promotes_when_llm_opts_in_without_rule_name(sample_email, decision_factory):
    """LLM path: rule_name is None, but decision.auto_mark=True. Should auto-mark."""
    decision = decision_factory(
        bucket=Bucket.IGNORE, confidence=0.95, rule_name=None, auto_mark=True, source="llm_fast"
    )
    result = _gate(sample_email(), decision, min_confidence=0.85)
    assert isinstance(result, AutoMarkResult)


def test_gate_exact_floor_passes(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.85, auto_mark=True)
    result = _gate(sample_email(), decision, min_confidence=0.85)
    assert isinstance(result, AutoMarkResult)

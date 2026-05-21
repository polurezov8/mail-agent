from __future__ import annotations

from mail_agent.graph.build import _gate
from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    SurfaceResult,
)


def test_gate_promotes_to_auto_mark(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.95)
    result = _gate(
        sample_email(),
        decision,
        auto_mark_lookup={"github": True},
        min_confidence=0.85,
    )
    assert isinstance(result, AutoMarkResult)


def test_gate_blocks_when_rule_does_not_opt_in(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.99)
    result = _gate(
        sample_email(),
        decision,
        auto_mark_lookup={"github": False},  # opt-out
        min_confidence=0.85,
    )
    assert isinstance(result, SurfaceResult)


def test_gate_blocks_when_below_confidence_floor(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.8)
    result = _gate(
        sample_email(),
        decision,
        auto_mark_lookup={"github": True},
        min_confidence=0.85,
    )
    assert isinstance(result, SurfaceResult)


def test_gate_blocks_when_bucket_is_not_ignore(sample_email, decision_factory):
    for bucket in (Bucket.RESPOND, Bucket.NOTIFY):
        decision = decision_factory(bucket=bucket, confidence=0.99)
        result = _gate(
            sample_email(),
            decision,
            auto_mark_lookup={"github": True},
            min_confidence=0.85,
        )
        assert isinstance(result, SurfaceResult)


def test_gate_blocks_when_rule_name_is_none(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.99, rule_name=None)
    result = _gate(
        sample_email(),
        decision,
        auto_mark_lookup={},
        min_confidence=0.85,
    )
    assert isinstance(result, SurfaceResult)


def test_gate_exact_floor_passes(sample_email, decision_factory):
    decision = decision_factory(bucket=Bucket.IGNORE, confidence=0.85)
    result = _gate(
        sample_email(),
        decision,
        auto_mark_lookup={"github": True},
        min_confidence=0.85,
    )
    assert isinstance(result, AutoMarkResult)

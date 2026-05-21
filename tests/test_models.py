from __future__ import annotations

import pytest
from pydantic import ValidationError

from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    SurfaceResult,
    TriageDecision,
)


def test_triage_decision_confidence_bounded():
    with pytest.raises(ValidationError):
        TriageDecision(
            bucket=Bucket.IGNORE,
            reasoning="x",
            confidence=1.1,
        )


def test_triage_decision_negative_confidence_rejected():
    with pytest.raises(ValidationError):
        TriageDecision(
            bucket=Bucket.IGNORE,
            reasoning="x",
            confidence=-0.1,
        )


def test_auto_mark_result_carries_kind_discriminator(sample_email, decision_factory):
    r = AutoMarkResult(email=sample_email(), decision=decision_factory())
    assert r.kind == "auto_mark"
    assert r.message_id == sample_email().id


def test_surface_result_carries_kind_discriminator(sample_email, decision_factory):
    r = SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.RESPOND))
    assert r.kind == "surface"


def test_email_from_domain_extraction(sample_email):
    assert sample_email(from_email="alice@github.com").from_domain == "github.com"
    assert sample_email(from_email="invalid").from_domain == ""


def test_bucket_enum_values():
    assert Bucket.IGNORE.value == "ignore"
    assert Bucket.NOTIFY.value == "notify"
    assert Bucket.RESPOND.value == "respond"

from __future__ import annotations

from mail_agent.graph.build import (
    _route_after_fetch,
    _route_after_persist,
    _route_to_slack_or_report,
)
from mail_agent.models import (
    AutoMarkResult,
    Bucket,
    SurfaceResult,
)


def test_route_after_fetch_skips_when_empty():
    assert _route_after_fetch({"inbox": []}) == "report"
    assert _route_after_fetch({}) == "report"


def test_route_after_fetch_proceeds_when_inbox_has_items(sample_email):
    assert _route_after_fetch({"inbox": [sample_email()]}) == "triage"


def test_route_after_persist_skips_mark_when_no_auto_mark(sample_email, decision_factory):
    state = {
        "results": [
            SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.RESPOND))
        ]
    }
    assert _route_after_persist(state) == "slack_dispatch"


def test_route_after_persist_marks_when_auto_mark_present(sample_email, decision_factory):
    state = {
        "results": [
            AutoMarkResult(email=sample_email(), decision=decision_factory()),
        ]
    }
    assert _route_after_persist(state) == "mark_read"


def test_route_to_slack_skips_when_mock(sample_email, decision_factory):
    state = {
        "mock": True,
        "results": [
            SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.RESPOND))
        ],
    }
    assert _route_to_slack_or_report(state) == "report"


def test_route_to_slack_skips_when_no_slack_flag(sample_email, decision_factory):
    state = {
        "no_slack": True,
        "results": [
            SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.RESPOND))
        ],
    }
    assert _route_to_slack_or_report(state) == "report"


def test_route_to_slack_skips_when_no_surface_results(sample_email, decision_factory):
    state = {
        "results": [
            AutoMarkResult(email=sample_email(), decision=decision_factory()),
        ]
    }
    assert _route_to_slack_or_report(state) == "report"


def test_route_to_slack_proceeds_when_surface_present(sample_email, decision_factory):
    state = {
        "results": [
            SurfaceResult(email=sample_email(), decision=decision_factory(bucket=Bucket.RESPOND))
        ]
    }
    assert _route_to_slack_or_report(state) == "slack_dispatch"

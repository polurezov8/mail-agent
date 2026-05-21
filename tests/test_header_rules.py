from __future__ import annotations

from mail_agent.config import Rule, RuleMatch
from mail_agent.models import Bucket
from mail_agent.rules.header import match_first


def test_from_domain_match_returns_rule_decision(sample_email):
    rules = [
        Rule(
            name="github",
            description="GH",
            match=RuleMatch(from_domain=["github.com"]),
            bucket=Bucket.IGNORE,
            auto_mark_read=True,
        )
    ]
    e = sample_email(from_email="notifications@github.com")
    decision = match_first(e, rules)
    assert decision is not None
    assert decision.bucket == Bucket.IGNORE
    assert decision.rule_name == "github"
    assert decision.confidence == 1.0
    assert decision.source == "header_rule"
    assert decision.auto_mark is True


def test_rule_without_auto_mark_opt_in_propagates_false(sample_email):
    rules = [
        Rule(
            name="linear",
            description="L",
            match=RuleMatch(from_domain=["linear.app"]),
            bucket=Bucket.NOTIFY,
            auto_mark_read=False,
        )
    ]
    decision = match_first(sample_email(from_email="bot@linear.app"), rules)
    assert decision is not None
    assert decision.auto_mark is False


def test_no_match_returns_none(sample_email):
    rules = [
        Rule(
            name="github",
            description="GH",
            match=RuleMatch(from_domain=["github.com"]),
            bucket=Bucket.IGNORE,
        )
    ]
    assert match_first(sample_email(from_email="ceo@yourco.test"), rules) is None


def test_header_contains_match(sample_email):
    rules = [
        Rule(
            name="calendar",
            description="Calendar invites",
            match=RuleMatch(headers_contains={"Content-Type": "text/calendar"}),
            bucket=Bucket.NOTIFY,
        )
    ]
    e = sample_email(headers={"Content-Type": "text/calendar; method=REQUEST"})
    decision = match_first(e, rules)
    assert decision is not None
    assert decision.rule_name == "calendar"


def test_subject_contains_match_is_case_insensitive(sample_email):
    rules = [
        Rule(
            name="recruiter",
            description="Cold outreach",
            match=RuleMatch(subject_contains=["opportunity"]),
            bucket=Bucket.IGNORE,
        )
    ]
    e = sample_email(subject="Exciting OPPORTUNITY for a CTO role")
    assert match_first(e, rules) is not None


def test_has_header_requires_all_listed(sample_email):
    rules = [
        Rule(
            name="newsletters",
            description="…",
            match=RuleMatch(has_header=["List-Unsubscribe"]),
            bucket=Bucket.IGNORE,
        )
    ]
    e_no = sample_email(headers={})
    e_yes = sample_email(headers={"List-Unsubscribe": "<mailto:x>"})
    assert match_first(e_no, rules) is None
    assert match_first(e_yes, rules) is not None


def test_first_match_wins(sample_email):
    rules = [
        Rule(
            name="first",
            description="catches everything",
            match=RuleMatch(),
            bucket=Bucket.NOTIFY,
        ),
        Rule(
            name="second",
            description="never reached",
            match=RuleMatch(),
            bucket=Bucket.IGNORE,
        ),
    ]
    decision = match_first(sample_email(), rules)
    assert decision is not None
    assert decision.rule_name == "first"


def test_from_email_match(sample_email):
    rules = [
        Rule(
            name="ceo",
            description="VIP",
            match=RuleMatch(from_email=["ceo@example.com"]),
            bucket=Bucket.RESPOND,
        )
    ]
    assert match_first(sample_email(from_email="CEO@example.com"), rules) is not None
    assert match_first(sample_email(from_email="other@example.com"), rules) is None

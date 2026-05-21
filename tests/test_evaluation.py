from __future__ import annotations


from mail_agent.config import Config
from mail_agent.evaluation.dataset import EvalReport, ExampleResult, LabeledExample
from mail_agent.evaluation.io import load, load_all, save
from mail_agent.evaluation.runner import evaluate
from mail_agent.models import Bucket


def _example(sample_email, expected_bucket, expected_rule=None, eid="e1"):
    return LabeledExample(
        email=sample_email(id=eid),
        expected_bucket=expected_bucket,
        expected_rule_name=expected_rule,
    )


def test_example_result_matched_bucket_and_rule(sample_email, decision_factory):
    ex = _example(sample_email, Bucket.IGNORE, "github")
    decision = decision_factory(bucket=Bucket.IGNORE, rule_name="github")
    r = ExampleResult.from_decision(ex, decision)
    assert r.matched_bucket
    assert r.matched_rule
    assert r.fully_matched


def test_example_result_rule_optional_when_none_expected(sample_email, decision_factory):
    ex = LabeledExample(
        email=sample_email(),
        expected_bucket=Bucket.IGNORE,
        expected_rule_name=None,
    )
    decision = decision_factory(bucket=Bucket.IGNORE, rule_name="any-rule")
    r = ExampleResult.from_decision(ex, decision)
    assert r.matched_rule  # None expected = any actual is fine


def test_eval_report_bucket_accuracy(sample_email, decision_factory):
    ok = ExampleResult.from_decision(
        _example(sample_email, Bucket.IGNORE),
        decision_factory(bucket=Bucket.IGNORE, rule_name=None),
    )
    bad = ExampleResult.from_decision(
        _example(sample_email, Bucket.RESPOND),
        decision_factory(bucket=Bucket.IGNORE, rule_name=None),
    )
    report = EvalReport(results=[ok, bad])
    assert report.bucket_correct == 1
    assert report.bucket_accuracy == 0.5
    assert len(report.failures) == 1


def test_eval_report_bucket_breakdown(sample_email, decision_factory):
    a = ExampleResult.from_decision(
        _example(sample_email, Bucket.IGNORE),
        decision_factory(bucket=Bucket.IGNORE, rule_name=None),
    )
    b = ExampleResult.from_decision(
        _example(sample_email, Bucket.NOTIFY),
        decision_factory(bucket=Bucket.NOTIFY, rule_name=None),
    )
    report = EvalReport(results=[a, b])
    bd = report.bucket_breakdown()
    assert bd[Bucket.IGNORE] == (1, 1)
    assert bd[Bucket.NOTIFY] == (1, 1)
    assert bd[Bucket.RESPOND] == (0, 0)


def test_io_roundtrip(tmp_path, sample_email):
    path = tmp_path / "fix.yaml"
    examples = [
        LabeledExample(email=sample_email(id="e1"), expected_bucket=Bucket.IGNORE),
        LabeledExample(email=sample_email(id="e2"), expected_bucket=Bucket.RESPOND),
    ]
    save(path, examples)
    loaded = load(path)
    assert len(loaded) == 2
    assert {ex.email.id for ex in loaded} == {"e1", "e2"}


def test_load_returns_empty_for_missing_file(tmp_path):
    assert load(tmp_path / "missing.yaml") == []


def test_load_all_merges_and_dedupes(tmp_path, sample_email):
    personal = tmp_path / "fixtures.yaml"
    public = tmp_path / "fixtures.public.yaml"
    save(
        personal,
        [LabeledExample(email=sample_email(id="e1"), expected_bucket=Bucket.IGNORE)],
    )
    save(
        public,
        [
            LabeledExample(email=sample_email(id="e1"), expected_bucket=Bucket.NOTIFY),
            LabeledExample(email=sample_email(id="e2"), expected_bucket=Bucket.RESPOND),
        ],
    )
    merged = load_all(personal)
    ids = [ex.email.id for ex in merged]
    assert ids == ["e1", "e2"]  # personal e1 wins, public e1 dropped, public e2 added


def test_evaluate_runs_rules_only_when_llm_disabled(
    sample_rules, llm_config, sample_email, monkeypatch
):
    llm_config.enabled = False
    cfg = Config(rules=sample_rules, llm=llm_config)
    examples = [
        LabeledExample(
            email=sample_email(from_email="x@github.com"),
            expected_bucket=Bucket.IGNORE,
            expected_rule_name="github",
        )
    ]
    report = evaluate(cfg, examples)
    assert report.bucket_correct == 1
    assert report.results[0].source == "header_rule"


def test_evaluate_uses_classifier_when_no_rule_matches(
    sample_rules, llm_config, sample_email, decision_factory, monkeypatch
):
    cfg = Config(rules=sample_rules, llm=llm_config)
    fake_decision = decision_factory(
        bucket=Bucket.RESPOND,
        rule_name=None,
        source="llm_fast",
        confidence=0.95,
        model="claude-haiku-test",
    )

    def fake_classify(email, rules, llm_cfg, thread_context=None):
        return fake_decision

    monkeypatch.setattr("mail_agent.evaluation.runner.classify", fake_classify)
    examples = [
        LabeledExample(
            email=sample_email(from_email="ceo@example.com"),
            expected_bucket=Bucket.RESPOND,
        )
    ]
    report = evaluate(cfg, examples)
    assert report.bucket_correct == 1
    assert report.results[0].source == "llm_fast"

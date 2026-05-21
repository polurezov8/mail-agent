from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import (
    Bucket,
    DecisionSource,
    EmailMessage,
    RuleName,
    TriageDecision,
)


class LabeledExample(BaseModel):
    """One ground-truth point. `expected_rule_name` is optional —
    None means any rule (or LLM with null rule) is acceptable as long as
    the bucket matches."""

    email: EmailMessage
    expected_bucket: Bucket
    expected_rule_name: RuleName | None = None
    notes: str | None = None


class ExampleResult(BaseModel):
    """One classifier run on a labeled example."""

    example: LabeledExample
    actual_bucket: Bucket
    actual_rule_name: RuleName | None
    confidence: float = Field(ge=0.0, le=1.0)
    source: DecisionSource

    @property
    def matched_bucket(self) -> bool:
        return self.actual_bucket == self.example.expected_bucket

    @property
    def matched_rule(self) -> bool:
        if self.example.expected_rule_name is None:
            return True
        return self.actual_rule_name == self.example.expected_rule_name

    @property
    def fully_matched(self) -> bool:
        return self.matched_bucket and self.matched_rule

    @classmethod
    def from_decision(cls, example: LabeledExample, decision: TriageDecision) -> ExampleResult:
        return cls(
            example=example,
            actual_bucket=decision.bucket,
            actual_rule_name=decision.rule_name,
            confidence=decision.confidence,
            source=decision.source,
        )


class EvalReport(BaseModel):
    results: list[ExampleResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def bucket_correct(self) -> int:
        return sum(1 for r in self.results if r.matched_bucket)

    @property
    def bucket_accuracy(self) -> float:
        if not self.results:
            return 1.0
        return self.bucket_correct / self.total

    @property
    def rule_examples(self) -> list[ExampleResult]:
        return [r for r in self.results if r.example.expected_rule_name is not None]

    @property
    def rule_accuracy(self) -> float:
        rule_ex = self.rule_examples
        if not rule_ex:
            return 1.0
        return sum(1 for r in rule_ex if r.matched_rule) / len(rule_ex)

    @property
    def failures(self) -> list[ExampleResult]:
        return [r for r in self.results if not r.fully_matched]

    def bucket_breakdown(self) -> dict[Bucket, tuple[int, int]]:
        """{bucket: (correct, total)} keyed on expected bucket."""
        out: dict[Bucket, tuple[int, int]] = {b: (0, 0) for b in Bucket}
        for r in self.results:
            c, t = out[r.example.expected_bucket]
            out[r.example.expected_bucket] = (c + (1 if r.matched_bucket else 0), t + 1)
        return out

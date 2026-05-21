from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, NewType, TypeAlias

from pydantic import BaseModel, Field


# Distinguish identifier kinds at the type level. Runtime = str.
MessageId = NewType("MessageId", str)
ThreadId = NewType("ThreadId", str)
AccountName = NewType("AccountName", str)
RuleName = NewType("RuleName", str)


class Bucket(str, Enum):
    IGNORE = "ignore"
    NOTIFY = "notify"
    RESPOND = "respond"


DecisionSource = Literal["header_rule", "llm_fast", "llm_smart", "user_correction"]


class EmailMessage(BaseModel):
    id: MessageId
    thread_id: ThreadId
    account: AccountName
    from_email: str
    from_name: str | None = None
    to: list[str] = Field(default_factory=list)
    subject: str
    snippet: str
    body: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    received_at: datetime

    @property
    def from_domain(self) -> str:
        return self.from_email.rsplit("@", 1)[-1].lower() if "@" in self.from_email else ""


class ThreadMessage(BaseModel):
    """Lightweight prior-message context for thread-aware classification."""

    from_email: str
    subject: str
    snippet: str
    received_at: datetime


class TriageDecision(BaseModel):
    """Classifier output. Same shape regardless of source."""

    bucket: Bucket
    rule_name: RuleName | None = Field(
        default=None,
        description="Rule that matched. None when no rule applies.",
    )
    reasoning: str = Field(description="One-sentence justification for the chosen bucket.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0–1.0. Below threshold → escalate to smart model.",
    )
    source: DecisionSource = "header_rule"
    model: str | None = Field(
        default=None,
        description="Concrete model ID when source is llm_*; None otherwise.",
    )


class AutoMarkResult(BaseModel):
    """Email decided to be auto-marked-read.

    Constructed ONLY when all safety gates passed:
      bucket == IGNORE, opted-in rule, confidence ≥ floor.
    `node_mark_read` accepts ONLY this variant — surface results
    can't accidentally reach the Gmail modify call.
    """

    kind: Literal["auto_mark"] = "auto_mark"
    email: EmailMessage
    decision: TriageDecision  # invariant: bucket == IGNORE

    @property
    def message_id(self) -> MessageId:
        return self.email.id


class SurfaceResult(BaseModel):
    """Email surfaced to user (digest / terminal). No side effects."""

    kind: Literal["surface"] = "surface"
    email: EmailMessage
    decision: TriageDecision


TriageResult: TypeAlias = AutoMarkResult | SurfaceResult

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .models import Bucket


class RuleMatch(BaseModel):
    from_domain: list[str] = Field(default_factory=list)
    from_email: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    has_header: list[str] = Field(default_factory=list)
    headers_contains: dict[str, str] = Field(default_factory=dict)


class Rule(BaseModel):
    name: str
    description: str
    match: RuleMatch
    bucket: Bucket
    auto_mark_read: bool = False


class LLMConfig(BaseModel):
    enabled: bool = True
    model_fast: str = "claude-haiku-4-5-20251001"
    model_smart: str = "claude-sonnet-4-6"
    confidence_threshold: float = 0.7
    auto_mark_min_confidence: float = 0.95
    buckets: list[Bucket] = Field(default_factory=lambda: list(Bucket))
    nl_rules: list[str] = Field(
        default_factory=list,
        description="Natural-language rules surfaced to the LLM at classification time.",
    )
    redact_pii: bool = Field(
        default=False,
        description=(
            "Redact emails / URLs / phones / IBAN / credit-card-shaped numbers in "
            "snippet + thread bodies before sending to the LLM. Sender address "
            "stays visible (needed for triage). Reasoning is re-hydrated."
        ),
    )
    redact_names: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit list of canonical full names to redact (e.g. 'Jane "
            "Doe'). Each entry + its ≥3-char parts all collapse to one "
            "`<PERSON_N>` placeholder, re-hydrated to the canonical form in the "
            "LLM's reasoning. Names not in this list are NOT redacted."
        ),
    )


class Config(BaseModel):
    rules: list[Rule]
    llm: LLMConfig


def load_config(path: str | Path = "config/rules.yaml") -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    return Config.model_validate(raw)

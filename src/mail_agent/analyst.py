"""Inbox analyst: answer free-text questions by fetching + reading email bodies."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from .config import LLMConfig

logger = logging.getLogger(__name__)

_BATCH_SIZE = 30


def _strip_html(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", no_tags).strip()

_PLAN_SYSTEM = """You translate a user's inbox question into a structured analysis plan.

Return three fields:
  gmail_query: Gmail search syntax string (from:, subject:, newer_than:Nd/Nw/Nm, etc.)
  extraction_instruction: what to extract from each email body — be specific (e.g. "extract: price in USD, billing date, item name")
  synthesis_instruction: how to combine extracted data to answer the question (e.g. "sum all prices, group by month, format as table")
  suggested_limit: integer 1-200 if the user implies a count, otherwise null

Examples:
  "subscriptions from Apple last 3 months, total cost"
    → gmail_query="from:apple.com newer_than:3m"
    → extraction_instruction="extract: item/subscription name, charge amount in USD, billing date. Return null if no price found."
    → synthesis_instruction="sum all charges, group by month, compute grand total"
    → suggested_limit=null

  "last 5 emails from my bank"
    → gmail_query="from:bank newer_than:6m"
    → extraction_instruction="extract: subject, date, key message summary"
    → synthesis_instruction="list emails chronologically"
    → suggested_limit=5
"""

_EXTRACT_SYSTEM = """You are an email data extractor. For each email, apply the extraction instruction and return structured JSON.

Each email has: message_id, subject, from_email, snippet (short Gmail preview), body (full text, may be messy).
Prefer body for data; fall back to snippet if body is empty or unhelpful.

For each email, return:
  "message_id": the id field from input
  "extracted": a dict with the extracted fields, or null ONLY if the email genuinely contains no relevant information

Be generous: partial data is better than null. If you can find an amount but not a date, return the amount.
Set null only for emails that are clearly unrelated (e.g. a marketing teaser with no purchase details).

Return ONLY a JSON array. No explanation. No markdown fences.

Example output:
[
  {"message_id": "abc123", "extracted": {"amount": 9.99, "currency": "USD", "date": "2026-04-01", "item": "iCloud+"}},
  {"message_id": "def456", "extracted": null}
]
"""

_SYNTHESISE_SYSTEM = """You answer inbox questions based on structured data extracted from emails.

You will receive:
  - The user's original question
  - A synthesis instruction
  - A JSON array of extraction results (extracted fields per email, nulls already filtered)

Return:
  answer: markdown string answering the question completely
  format_hint: one of "table", "bullet_list", "paragraph", "number"
    - "number": answer is a single number or amount
    - "table": answer is a comparison across items/dates
    - "bullet_list": answer is a list of items
    - "paragraph": freeform prose
  source_count: integer — how many emails contributed to the answer
"""


class AnalysisPlan(BaseModel):
    gmail_query: str
    extraction_instruction: str
    synthesis_instruction: str
    suggested_limit: int | None = Field(default=None, ge=1, le=200)


class ExtractionResult(BaseModel):
    message_id: str
    from_email: str
    subject: str
    received_at: datetime
    extracted: dict | None


class AnalysisAnswer(BaseModel):
    answer: str
    format_hint: Literal["table", "bullet_list", "paragraph", "number"]
    source_count: int


def build_analysis_plan(question: str, cfg: LLMConfig) -> AnalysisPlan:
    model = ChatAnthropic(model=cfg.model_fast, temperature=0)
    structured = model.with_structured_output(AnalysisPlan)
    return structured.invoke(
        [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": question},
        ]
    )


def extract_batch(
    emails: list[dict],  # each: {message_id, from_email, subject, received_at, snippet, body}
    instruction: str,
    cfg: LLMConfig,
) -> list[dict]:
    """Run extraction on up to _BATCH_SIZE emails. Returns raw dicts from LLM."""
    model = ChatAnthropic(model=cfg.model_fast, temperature=0)
    payload = json.dumps(emails, default=str)
    response = model.invoke(
        [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": f"Extraction instruction: {instruction}\n\nEmails:\n{payload}",
            },
        ]
    )
    text = response.content if hasattr(response, "content") else str(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("extract_batch: JSON parse failed, returning empty batch")
        return []


def synthesise(
    results: list[ExtractionResult],
    instruction: str,
    question: str,
    cfg: LLMConfig,
) -> AnalysisAnswer:
    model = ChatAnthropic(model=cfg.model_smart, temperature=0)
    structured = model.with_structured_output(AnalysisAnswer)
    relevant = [r for r in results if r.extracted is not None]
    payload = json.dumps(
        [
            {
                "message_id": r.message_id,
                "from_email": r.from_email,
                "subject": r.subject,
                "received_at": r.received_at.isoformat(),
                "extracted": r.extracted,
            }
            for r in relevant
        ]
    )
    return structured.invoke(
        [
            {"role": "system", "content": _SYNTHESISE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n"
                    f"Synthesis instruction: {instruction}\n"
                    f"Extracted data ({len(relevant)} emails):\n{payload}"
                ),
            },
        ]
    )


def analyse_inbox(question: str, cfg: LLMConfig) -> AnalysisAnswer:
    """Orchestrate the full analysis pipeline."""
    from .gmail.accounts import load_accounts
    from .gmail.client import fetch_message_body, search_messages

    plan = build_analysis_plan(question, cfg)
    if not plan.gmail_query.strip():
        return AnalysisAnswer(
            answer="Couldn't build a Gmail query from that question. Try rephrasing.",
            format_hint="paragraph",
            source_count=0,
        )

    limit = plan.suggested_limit or 50
    accounts = [a for a in load_accounts() if a.is_authorized]
    if not accounts:
        return AnalysisAnswer(
            answer="No authorized Gmail accounts.",
            format_hint="paragraph",
            source_count=0,
        )

    messages = []
    for acct in accounts:
        try:
            messages.extend(search_messages(acct, plan.gmail_query, limit=limit))
        except Exception:
            logger.exception("search_messages failed for account %s", acct.name)

    if not messages:
        return AnalysisAnswer(
            answer=f"No emails matched the query `{plan.gmail_query}`.",
            format_hint="paragraph",
            source_count=0,
        )

    # Fetch bodies + build batch inputs
    email_inputs: list[dict] = []
    for msg in messages:
        acct = next((a for a in accounts if a.name == msg.account), accounts[0])
        try:
            raw_body = fetch_message_body(acct, msg.id)
        except Exception:
            logger.warning("fetch_message_body failed for %s, using snippet", msg.id)
            raw_body = ""

        # Strip HTML tags so the LLM sees plain text, not tag soup
        body = _strip_html(raw_body) if raw_body.lstrip().startswith("<") else raw_body

        email_inputs.append(
            {
                "message_id": msg.id,
                "from_email": msg.from_email,
                "subject": msg.subject,
                "received_at": msg.received_at.isoformat(),
                "snippet": msg.snippet,
                "body": body or msg.snippet,
            }
        )

    # Extract in batches
    all_extractions: list[ExtractionResult] = []
    for i in range(0, len(email_inputs), _BATCH_SIZE):
        batch = email_inputs[i : i + _BATCH_SIZE]
        raw_results = extract_batch(batch, plan.extraction_instruction, cfg)
        msg_map = {e["message_id"]: e for e in batch}
        for raw in raw_results:
            mid = raw.get("message_id", "")
            source = msg_map.get(mid, {})
            all_extractions.append(
                ExtractionResult(
                    message_id=mid,
                    from_email=source.get("from_email", ""),
                    subject=source.get("subject", ""),
                    received_at=datetime.fromisoformat(
                        source.get("received_at", datetime.now(timezone.utc).isoformat())
                    ),
                    extracted=raw.get("extracted"),
                )
            )

    relevant_count = sum(1 for r in all_extractions if r.extracted is not None)
    if relevant_count == 0:
        return AnalysisAnswer(
            answer=(
                f"Found {len(messages)} email(s) matching your query but none contained "
                "the information needed to answer your question."
            ),
            format_hint="paragraph",
            source_count=0,
        )

    return synthesise(all_extractions, plan.synthesis_instruction, question, cfg)

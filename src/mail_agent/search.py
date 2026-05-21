"""Natural-language inbox search.

LLM translates a free-text query into a Gmail search expression
(`from:`, `subject:`, `newer_than:`, etc.) — Gmail itself runs the search.
Read-only by construction: the `q` parameter on `messages.list` never modifies."""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from .config import LLMConfig

GMAIL_QUERY_SYSTEM = """You translate natural-language mail queries to Gmail search syntax.

Gmail operators you may use:
  from:EMAIL_OR_DOMAIN       (e.g. from:jane@example.com, from:linkedin.com)
  to:EMAIL                   recipient match
  subject:WORDS              subject contains
  has:attachment             messages with attachments
  label:NAME                 in a Gmail label
  in:inbox | in:anywhere     scope
  is:unread | is:read | is:starred | is:important
  newer_than:Nd | older_than:Nd     N days
  newer_than:Nw | older_than:Nw     N weeks
  newer_than:Nm | older_than:Nm     N months
  "exact phrase"             quoted exact-phrase match
  OR, parens                 boolean

Return two fields:
  • gmail_query: ONLY the Gmail search string, no surrounding quotes, no explanation.
  • suggested_limit: integer 1-50 if the user named a count ("last 3", "top 5",
    "find 10 mails"), otherwise null. Default behavior (when null) is the
    caller-supplied limit.

Examples:
  "anything from Jane last week"     ->  query="from:jane newer_than:7d", limit=null
  "find last 3 mail from linkedin"   ->  query="from:linkedin.com", limit=3
  "top 5 invoices with attachments"  ->  query="subject:invoice has:attachment", limit=5
  "stripe receipts older than a month" ->  query="from:stripe.com older_than:1m", limit=null
"""


class SearchPlan(BaseModel):
    """Structured output: Gmail `q` string + a one-line reasoning."""

    gmail_query: str = Field(description="Gmail search syntax, ready for messages.list q=...")
    reasoning: str = Field(description="One short sentence explaining the translation.")
    suggested_limit: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="If the user named a count, the requested N. Otherwise null.",
    )


def build_gmail_query(nl_query: str, cfg: LLMConfig) -> SearchPlan:
    """Use the LLM to map an NL query to Gmail search syntax."""
    if not nl_query.strip():
        return SearchPlan(gmail_query="", reasoning="empty query")
    model = ChatAnthropic(model=cfg.model_fast, temperature=0)
    structured = model.with_structured_output(SearchPlan)
    return structured.invoke(
        [
            {"role": "system", "content": GMAIL_QUERY_SYSTEM},
            {"role": "user", "content": nl_query},
        ]
    )

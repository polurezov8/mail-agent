# Inbox Analyst — Design Spec

**Date:** 2026-05-21  
**Status:** approved  
**Feature:** "Ask anything about your inbox" — free-text DM questions answered by a multi-stage pipeline that fetches, reads, and synthesises real email bodies.

---

## Problem

`/mail search` returns metadata cards (from, subject, snippet). It can't answer questions that require reading body content and aggregating across emails — e.g. "sum up Apple subscription charges from the last 3 months."

## Scope

Single-turn, read-only. User asks a question in a bot DM. Agent fetches matching emails, reads bodies, extracts structured data, synthesises an answer, posts it back. No conversation state. No email sending. No modifications.

---

## Architecture

Five stages, sequential per question:

```
DM free text
    ↓
handle_ask() [commands.py]
    ↓
analyse_inbox(question, cfg) [analyst.py]
    ├─ [1] build_analysis_plan()   Haiku → AnalysisPlan
    ├─ [2] search_messages() + fetch_message_body() per message
    ├─ [3] extract_batch() × batches of 30   Haiku → list[ExtractionResult]
    ├─ [4] synthesise()            Sonnet → AnalysisAnswer
    └─ [5] post ask_result_blocks() to DM channel
```

---

## Components

### `src/mail_agent/analyst.py` (NEW)

**Models:**

```python
class AnalysisPlan(BaseModel):
    gmail_query: str
    extraction_instruction: str   # what to pull from each email body
    synthesis_instruction: str    # how to combine extracted data into an answer
    suggested_limit: int | None = None  # None → 50

class ExtractionResult(BaseModel):
    message_id: str
    from_email: str
    subject: str
    received_at: datetime
    extracted: dict | None        # structured data; None if email is irrelevant

class AnalysisAnswer(BaseModel):
    answer: str                   # markdown, ready for Slack
    format_hint: Literal["table", "bullet_list", "paragraph", "number"]
    source_count: int             # N emails read
```

**Functions:**

- `build_analysis_plan(question, cfg) -> AnalysisPlan` — Haiku with structured output. Translates question to Gmail query + instructions for extract and synthesise steps.
- `extract_batch(messages_with_bodies, instruction, cfg) -> list[ExtractionResult]` — Haiku. Given up to 30 (subject, snippet, body) tuples + the extraction instruction, returns per-message structured data or None for irrelevant messages.
- `synthesise(results, instruction, question, cfg) -> AnalysisAnswer` — Sonnet. All ExtractionResults passed as JSON. Returns answer text + format hint.
- `analyse_inbox(question, cfg) -> AnalysisAnswer` — orchestrator. Calls the above in order, handles pagination via batches.

### `src/mail_agent/gmail/client.py` (EXTEND)

Add `fetch_message_body(account, message_id) -> str`:
- `.get(userId="me", id=message_id, format="full")` 
- Recursively walks MIME parts, collects `text/plain` parts (prefer over `text/html`)
- Returns joined plain text, truncated at 8 000 chars to cap per-message token cost

### `src/mail_agent/slack/commands.py` (EXTEND)

- Add `_run_ask_background(respond, question)` — mirrors `_run_search_background` pattern; calls `analyse_inbox()`, posts result, catches exceptions
- Add `handle_ask(respond, question)` — acknowledge + start thread
- Change `dispatch()`: `fallback_to_search` → `fallback_to_ask`; `handle_search(respond, text)` fallback becomes `handle_ask(respond, text)`
- Update `HELP_TEXT`: DM tip → "type any question about your inbox"

### `src/mail_agent/slack/blocks.py` (EXTEND)

Add `ask_result_blocks(question, answer: AnalysisAnswer) -> list[dict]`:
- Header block: question (truncated 150 chars)
- Body block: render `answer.answer` as Slack markdown
- Footer: `_Based on {source_count} emails_`
- `format_hint` influences layout: `number` → large text, `table` → code block, others → markdown section

---

## Data Flow

1. User types: `"check subscriptions from Apple last 3 months and total the cost"`
2. `build_analysis_plan` → `gmail_query = "from:apple.com newer_than:3m"`, `extraction_instruction = "extract: item name, price (USD), billing date"`, `synthesis_instruction = "sum all prices, group by month"`
3. `search_messages` on each authorized account with `limit = plan.suggested_limit or 50`
4. For each message: `fetch_message_body` (format=full, text/plain, ≤8 000 chars)
5. Batch into groups of 30. For each batch: `extract_batch` → list of ExtractionResults
6. `synthesise(all_results, ...)` → `AnalysisAnswer(answer="Total: $47.97...", format_hint="table", source_count=12)`
7. `ask_result_blocks` → `chat_postMessage` to DM channel

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| No authorized accounts | Respond ephemeral: ":warning: No authorized Gmail accounts." |
| Gmail API error on search | Respond ephemeral with account name + error; abort |
| `fetch_message_body` fails for one message | Log + skip that message; continue with remaining |
| Zero relevant emails after extract | Answer: "Found N emails matching your query but none contained the information needed." |
| LLM structured output parse failure in plan step | Respond ephemeral: ":warning: Couldn't interpret that question. Try rephrasing." |
| LLM error in extract/synthesise | Respond ephemeral with error; no partial post |
| Question is too vague (empty gmail_query) | Respond ephemeral: ":warning: Couldn't build a query from that." |

All errors: log via stdlib `logging`, never raise to Bolt handler.

---

## Testing

**`tests/test_analyst.py`** (NEW):

1. `test_build_analysis_plan_returns_valid_query` — patch LLM, assert `gmail_query` non-empty, `extraction_instruction` non-empty
2. `test_extract_batch_marks_irrelevant` — pass emails with no price data to extract instruction about prices, assert `extracted=None` for at least one
3. `test_extract_batch_extracts_structured_data` — pass email with known price in body, assert `extracted["price"]` present
4. `test_synthesise_returns_answer` — pass pre-built ExtractionResults, assert `answer` non-empty, `format_hint` in valid set
5. `test_analyse_inbox_end_to_end` — patch `build_analysis_plan`, `search_messages`, `fetch_message_body`, `extract_batch`, `synthesise`; assert orchestrator calls them in order and returns `AnalysisAnswer`
6. `test_analyse_inbox_skips_body_fetch_failure` — `fetch_message_body` raises on one message; assert remaining messages still processed

**`tests/test_fetch_message_body.py`** (NEW or in `test_gmail_client.py`):

1. `test_fetch_message_body_plain_text` — mock API response with `text/plain` part, assert body returned
2. `test_fetch_message_body_prefers_plain_over_html` — multipart with both, assert plain returned
3. `test_fetch_message_body_truncates_at_8000` — body > 8 000 chars, assert `len(result) == 8_000`

---

## Constraints

- Read-only. `analyse_inbox` never calls any Gmail modify API.
- No conversation state. Each question is independent.
- Token cost cap: 8 000 chars per body × 30 per batch = ~240 000 chars per Haiku call. Acceptable.
- Sonnet only for final synthesis, not per-batch extraction.
- No eval harness in this iteration. Correctness judged by manual spot-check.

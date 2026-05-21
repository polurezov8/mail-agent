from __future__ import annotations

import base64
import html
import os
from datetime import datetime, timezone
from email.utils import parseaddr

from googleapiclient.discovery import build

from ..models import AccountName, EmailMessage, MessageId, ThreadId, ThreadMessage
from .accounts import Account
from .auth import get_credentials

_BODY_TRUNCATE = 8_000


def _service(account: Account):
    creds = get_credentials(account, interactive=False)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _headers_dict(payload: dict) -> dict[str, str]:
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


def _parse_message(account: AccountName, raw: dict) -> EmailMessage:
    payload = raw.get("payload", {})
    headers = _headers_dict(payload)
    from_raw = headers.get("From", "")
    from_name, from_email = parseaddr(from_raw)
    to_raw = headers.get("To", "")
    to_list = [addr for _, addr in [parseaddr(p) for p in to_raw.split(",")] if addr]
    internal_ms = int(raw.get("internalDate", "0"))
    received_at = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
    return EmailMessage(
        id=MessageId(raw["id"]),
        thread_id=ThreadId(raw.get("threadId", raw["id"])),
        account=account,
        from_email=from_email or "",
        from_name=from_name or None,
        to=to_list,
        subject=html.unescape(headers.get("Subject", "")),
        snippet=html.unescape(raw.get("snippet", "")),
        body="",  # snippet sufficient for Phase 1 triage
        headers=headers,
        received_at=received_at,
    )


def _extract_text_from_payload(payload: dict) -> str:
    """Recursively walk MIME parts, return text/plain (or text/html fallback)."""
    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
    if mime == "text/html" and body_data:
        raw = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        return html.unescape(raw)

    parts = payload.get("parts", [])
    plain_texts: list[str] = []
    html_texts: list[str] = []
    for part in parts:
        candidate = _extract_text_from_payload(part)
        if candidate:
            if "html" in part.get("mimeType", ""):
                html_texts.append(candidate)
            else:
                plain_texts.append(candidate)

    if plain_texts:
        return "\n".join(plain_texts)
    if html_texts:
        return "\n".join(html_texts)
    return ""


def fetch_message_body(account: Account, message_id: MessageId) -> str:
    """Fetch full body of a single message. Returns plain text, truncated at 8 000 chars."""
    svc = _service(account)
    raw = (
        svc.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    text = _extract_text_from_payload(raw.get("payload", {}))
    return text[:_BODY_TRUNCATE]


def search_messages(
    account: Account,
    query: str,
    limit: int = 10,
) -> list[EmailMessage]:
    """Read-only Gmail search. `query` is Gmail search syntax (the `q` param)."""
    if not query.strip():
        return []
    svc = _service(account)
    listing = (
        svc.users()
        .messages()
        .list(userId="me", q=query, maxResults=limit)
        .execute()
    )
    ids = [m["id"] for m in listing.get("messages", [])]
    acct_name = AccountName(account.name)
    messages: list[EmailMessage] = []
    for mid in ids:
        raw = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            )
            .execute()
        )
        messages.append(_parse_message(acct_name, raw))
    return messages


def fetch_thread_history(
    account: Account,
    thread_id: ThreadId,
    exclude_message_id: MessageId | None = None,
    limit: int = 3,
) -> list[ThreadMessage]:
    """Return up to `limit` prior messages in the thread, oldest→newest, excluding
    the current message. Empty list when the thread has no other messages."""
    svc = _service(account)
    try:
        raw = (
            svc.users()
            .threads()
            .get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
    except Exception:
        return []
    messages = raw.get("messages", [])
    prior: list[ThreadMessage] = []
    for m in messages:
        if exclude_message_id and m.get("id") == exclude_message_id:
            continue
        headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
        from_raw = headers.get("From", "")
        _, from_email = parseaddr(from_raw)
        internal_ms = int(m.get("internalDate", "0"))
        received_at = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
        prior.append(
            ThreadMessage(
                from_email=html.unescape(from_email or ""),
                subject=html.unescape(headers.get("Subject", "")),
                snippet=html.unescape(m.get("snippet", "")),
                received_at=received_at,
            )
        )
    # Most recent N, sorted oldest→newest
    prior.sort(key=lambda x: x.received_at)
    return prior[-limit:] if limit > 0 else prior


def fetch_unread(
    account: Account,
    limit: int | None = None,
    query: str | None = None,
) -> list[EmailMessage]:
    """Fetch messages matching `query` (default: unread in inbox). Excludes spam/trash."""
    limit = limit or int(os.environ.get("GMAIL_FETCH_LIMIT", "50"))
    q = query or "is:unread in:inbox"
    svc = _service(account)
    listing = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
    ids = [m["id"] for m in listing.get("messages", [])]
    acct_name = AccountName(account.name)
    messages: list[EmailMessage] = []
    for mid in ids:
        raw = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=[
                    "From",
                    "To",
                    "Subject",
                    "Date",
                    "List-Unsubscribe",
                    "List-Unsubscribe-Post",
                    "Content-Type",
                ],
            )
            .execute()
        )
        messages.append(_parse_message(acct_name, raw))
    return messages

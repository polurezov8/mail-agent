"""Bootstrap eval fixtures from recent processed mails.

Reads `processed_messages` for an account, refetches email metadata via Gmail,
emits LabeledExample stubs where `expected_*` = the agent's prior verdict.
Caller reviews and corrects labels in the YAML before running eval."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from googleapiclient.discovery import build

from ..gmail.accounts import Account, load_accounts
from ..gmail.auth import get_credentials
from ..gmail.client import _parse_message  # internal helper reuse, single repo
from ..models import AccountName, Bucket, EmailMessage, MessageId, RuleName
from .dataset import LabeledExample


def _db_path() -> Path:
    return Path(os.environ.get("GMAIL_DB_PATH", "./mail_agent.db"))


def _recent_processed(
    account: AccountName, limit: int
) -> list[tuple[MessageId, Bucket, RuleName | None]]:
    if not _db_path().exists():
        return []
    conn = sqlite3.connect(_db_path())
    try:
        rows = conn.execute(
            "SELECT message_id, bucket, rule_name FROM processed_messages "
            "WHERE account = ? ORDER BY processed_at DESC LIMIT ?",
            (account, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        (MessageId(mid), Bucket(bucket), RuleName(rn) if rn else None) for mid, bucket, rn in rows
    ]


def _fetch_email(account: Account, message_id: MessageId) -> EmailMessage | None:
    creds = get_credentials(account, interactive=False)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    raw = (
        svc.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date", "List-Unsubscribe", "Content-Type"],
        )
        .execute()
    )
    return _parse_message(AccountName(account.name), raw)


def seed_from_processed(account_name: str, limit: int) -> list[LabeledExample]:
    accounts = {a.name: a for a in load_accounts()}
    account = accounts.get(account_name)
    if account is None:
        raise ValueError(f"Unknown account '{account_name}'. Configured: {list(accounts)}")
    if not account.is_authorized:
        raise RuntimeError(
            f"Account '{account_name}' not authorized. Run `mail-agent setup-gmail`."
        )

    rows = _recent_processed(AccountName(account_name), limit)
    examples: list[LabeledExample] = []
    for message_id, bucket, rule_name in rows:
        try:
            email = _fetch_email(account, message_id)
        except Exception as exc:
            print(f"[seed] skipping {message_id}: {exc}")
            continue
        if email is None:
            continue
        examples.append(
            LabeledExample(
                email=email,
                expected_bucket=bucket,
                expected_rule_name=rule_name,
                notes=None,
            )
        )
    return examples

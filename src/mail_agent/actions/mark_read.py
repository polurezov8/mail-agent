from __future__ import annotations

from googleapiclient.discovery import build

from ..gmail.accounts import Account
from ..gmail.auth import get_credentials
from ..models import MessageId


def mark_read(account: Account, message_ids: list[MessageId]) -> int:
    """Remove UNREAD label from messages. Returns count submitted.

    Single batchModify call (up to 1000 IDs).
    """
    if not message_ids:
        return 0
    creds = get_credentials(account, interactive=False)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    svc.users().messages().batchModify(
        userId="me",
        body={"ids": list(message_ids), "removeLabelIds": ["UNREAD"]},
    ).execute()
    return len(message_ids)

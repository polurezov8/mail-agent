from __future__ import annotations

import os
from functools import lru_cache

from slack_sdk import WebClient

from .types import SlackChannelId, SlackUserId


def is_configured() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN"))


def get_client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN not set")
    return WebClient(token=token)


@lru_cache(maxsize=1)
def get_channel_id() -> SlackChannelId:
    """Resolve the target channel. Explicit SLACK_DIGEST_CHANNEL wins;
    otherwise opens DM with SLACK_USER_ID."""
    explicit = os.environ.get("SLACK_DIGEST_CHANNEL", "").strip()
    if explicit:
        return SlackChannelId(explicit)
    user = os.environ.get("SLACK_USER_ID", "").strip()
    if not user:
        raise RuntimeError(
            "Neither SLACK_DIGEST_CHANNEL nor SLACK_USER_ID set. "
            "Set SLACK_USER_ID to your Slack member ID for DM delivery."
        )
    client = get_client()
    resp = client.conversations_open(users=SlackUserId(user))
    return SlackChannelId(resp["channel"]["id"])

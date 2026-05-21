from __future__ import annotations

from typing import NewType, TypedDict

# Slack-side identifiers, distinguished from Gmail/local ones at the type level.
SlackChannelId = NewType("SlackChannelId", str)
SlackUserId = NewType("SlackUserId", str)
SlackTimestamp = NewType("SlackTimestamp", str)


class DispatchSummary(TypedDict):
    realtime_posts: int  # respond-bucket individual messages
    digest_mails: int  # notify-bucket mails included in single digest
    skipped: int  # ignore-bucket (silent) + missing-config

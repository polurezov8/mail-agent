"""Modal builders for Slack views (correction flow)."""

from __future__ import annotations

import json


def correction_modal(
    account: str,
    message_id: str,
    from_email: str,
    subject: str,
    original_bucket: str,
) -> dict:
    metadata = json.dumps(
        {
            "account": account,
            "message_id": message_id,
            "from_email": from_email,
            "subject": subject,
            "original_bucket": original_bucket,
        }
    )
    return {
        "type": "modal",
        "callback_id": "correction_modal",
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "Correct bucket"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Subject:* {subject[:120]}\n"
                        f"*From:* {from_email}\n"
                        f"*Currently classified as:* `{original_bucket}`"
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "bucket_block",
                "label": {"type": "plain_text", "text": "Correct bucket"},
                "element": {
                    "type": "static_select",
                    "action_id": "bucket_select",
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Ignore"},
                            "description": {
                                "type": "plain_text",
                                "text": "Auto-marked as read · not surfaced in Slack",
                            },
                            "value": "ignore",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Notify"},
                            "description": {
                                "type": "plain_text",
                                "text": "Surfaced in digest · left unread · you decide",
                            },
                            "value": "notify",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Respond"},
                            "description": {
                                "type": "plain_text",
                                "text": "Realtime Slack ping · needs your reply",
                            },
                            "value": "respond",
                        },
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "apply_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Scope"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "apply_checkbox",
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Apply to all future mail from this sender",
                            },
                            "value": "apply_to_sender",
                        }
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "note_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Note (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "note_input",
                    "multiline": True,
                    "max_length": 200,
                },
            },
        ],
    }

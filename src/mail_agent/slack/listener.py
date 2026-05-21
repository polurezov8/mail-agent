"""Socket Mode listener. Long-running process — handles Mark-read button clicks
and feedback modals.

Run with: `mail-agent slack listen`.
"""

from __future__ import annotations

import json
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..actions.mark_read import mark_read
from ..evaluation.seed import _fetch_email
from ..gmail.accounts import load_accounts
from ..models import Bucket, MessageId
from ..store.sqlite import _conn, _db_path, init_db, log_correction
from .corrections import append_correction_to_fixtures
from .modals import correction_modal


def _lookup_email_meta(account_name: str, message_id: str, logger) -> tuple[str, str]:
    """Best-effort metadata lookup. Never raises — returns placeholders if all sources fail."""
    # 1. DB (mark_read_audit) — fast hit for already-auto-marked mail.
    if _db_path().exists():
        try:
            with _conn() as conn:
                row = conn.execute(
                    "SELECT from_email, subject FROM mark_read_audit "
                    "WHERE account = ? AND message_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (account_name, message_id),
                ).fetchone()
            if row and row[0]:
                return row[0], row[1] or "(no subject)"
        except Exception as exc:
            logger.warning(f"db meta lookup failed: {exc}")

    # 2. Gmail API.
    try:
        accounts = {a.name: a for a in load_accounts()}
        account = accounts.get(account_name)
        if account and account.is_authorized:
            email = _fetch_email(account, MessageId(message_id))
            if email:
                return email.from_email, email.subject
    except Exception as exc:
        logger.warning(f"gmail meta lookup failed: {exc}")

    # 3. Give up gracefully.
    return "(unknown sender)", "(unknown subject)"


def _build_app() -> App:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN not set")
    app = App(token=token)
    init_db()

    @app.action("mark_read")
    def handle_mark_read(ack, body, client, logger) -> None:
        ack()
        try:
            value = body["actions"][0]["value"]
            account_name, message_id = value.split(":", 1)
            accounts = {a.name: a for a in load_accounts()}
            account = accounts.get(account_name)
            if account is None:
                logger.error(f"unknown account: {account_name}")
                return
            mark_read(account, [MessageId(message_id)])

            # Replace only the clicked row's actions block with a confirmation.
            # Using block_id scopes the replacement to the one clicked row so
            # other rows in the same digest message keep their buttons.
            clicked_block_id = body["actions"][0].get("block_id")
            channel = body["channel"]["id"]
            ts = body["message"]["ts"]
            blocks = body["message"]["blocks"]
            updated_blocks = []
            for block in blocks:
                is_clicked_actions = (
                    block.get("type") == "actions"
                    and block.get("block_id") == clicked_block_id
                )
                if is_clicked_actions:
                    updated_blocks.append(
                        {
                            "type": "context",
                            "elements": [{"type": "mrkdwn", "text": "✅ Marked as read"}],
                        }
                    )
                elif block.get("type") == "section" and "accessory" in block:
                    new_block = {k: v for k, v in block.items() if k != "accessory"}
                    updated_blocks.append(new_block)
                else:
                    updated_blocks.append(block)
            client.chat_update(channel=channel, ts=ts, blocks=updated_blocks, text="Mail handled")
        except Exception as exc:
            logger.exception(f"mark_read action failed: {exc}")

    @app.action("open_gmail")
    def handle_open_gmail(ack) -> None:
        ack()  # URL handled client-side; nothing to do server-side

    @app.action("row_overflow")
    def handle_row_overflow(ack, body, client, logger) -> None:
        ack()
        try:
            selected = body["actions"][0]["selected_option"]["value"]
            parts = selected.split(":", 2)
            if len(parts) == 3:
                account_name, message_id, original_bucket = parts
                from_email, subject = _lookup_email_meta(account_name, message_id, logger)
                client.views_open(
                    trigger_id=body["trigger_id"],
                    view=correction_modal(
                        account=account_name,
                        message_id=message_id,
                        from_email=from_email,
                        subject=subject,
                        original_bucket=original_bucket,
                    ),
                )
            else:
                logger.warning(f"row_overflow: unhandled value: {selected!r}")
        except Exception as exc:
            logger.exception(f"row_overflow action failed: {exc}")

    @app.action("mark_read_bulk")
    def handle_mark_read_bulk(ack, body, client, logger) -> None:
        ack()
        try:
            value = body["actions"][0]["value"]
            accounts_map = {a.name: a for a in load_accounts()}
            for pair in value.split(","):
                pair = pair.strip()
                if ":" not in pair:
                    continue
                account_name, message_id = pair.split(":", 1)
                account = accounts_map.get(account_name)
                if account:
                    mark_read(account, [MessageId(message_id)])

            clicked_block_id = body["actions"][0].get("block_id")
            channel = body["channel"]["id"]
            ts = body["message"]["ts"]
            blocks = body["message"]["blocks"]
            updated_blocks = [
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "✅ Marked as read"}],
                }
                if block.get("type") == "actions" and block.get("block_id") == clicked_block_id
                else block
                for block in blocks
            ]
            client.chat_update(channel=channel, ts=ts, blocks=updated_blocks, text="Mail handled")
        except Exception as exc:
            logger.exception(f"mark_read_bulk action failed: {exc}")

    @app.action("correct_bucket")
    def handle_correct_bucket(ack, body, client, logger) -> None:
        ack()
        try:
            value = body["actions"][0]["value"]
            parts = value.split(":", 2)
            if len(parts) != 3:
                logger.error(f"malformed correct_bucket value: {value}")
                return
            account_name, message_id, original_bucket = parts

            # Resolve metadata with graceful fallback chain:
            #   1. DB (mark_read_audit) — fast, works for auto-marked mail.
            #   2. Gmail API — works for any real message ID.
            #   3. Placeholders — never block the modal.
            from_email, subject = _lookup_email_meta(account_name, message_id, logger)

            client.views_open(
                trigger_id=body["trigger_id"],
                view=correction_modal(
                    account=account_name,
                    message_id=message_id,
                    from_email=from_email,
                    subject=subject,
                    original_bucket=original_bucket,
                ),
            )
        except Exception as exc:
            logger.exception(f"correct_bucket action failed: {exc}")

    @app.view("correction_modal")
    def handle_correction_submit(ack, body, view, client, logger) -> None:
        ack()
        try:
            metadata = json.loads(view["private_metadata"])
            state = view["state"]["values"]
            bucket_value = state["bucket_block"]["bucket_select"]["selected_option"]["value"]
            note = state.get("note_block", {}).get("note_input", {}).get("value")
            apply_options = (
                state.get("apply_block", {}).get("apply_checkbox", {}).get("selected_options") or []
            )
            apply_to_sender = any(opt.get("value") == "apply_to_sender" for opt in apply_options)

            log_correction(
                account=metadata["account"],
                message_id=metadata["message_id"],
                original_bucket=metadata["original_bucket"],
                original_rule=None,
                corrected_bucket=bucket_value,
                note=note,
                from_email=metadata.get("from_email"),
                subject=metadata.get("subject"),
                apply_to_sender=apply_to_sender,
            )

            append_correction_to_fixtures(
                account_name=metadata["account"],
                message_id=metadata["message_id"],
                corrected_bucket=Bucket(bucket_value),
                note=note,
            )

            # DM the user a small confirmation.
            try:
                user_id = body["user"]["id"]
                im = client.conversations_open(users=user_id)
                scope = (
                    "applied to all future mail from this sender"
                    if apply_to_sender
                    else "this message only"
                )
                client.chat_postMessage(
                    channel=im["channel"]["id"],
                    text=(
                        f"✅ Correction saved: `{metadata['original_bucket']}` → `{bucket_value}` "
                        f"({scope})."
                    ),
                )
            except Exception:
                pass
        except Exception as exc:
            logger.exception(f"correction_modal submit failed: {exc}")

    @app.command("/mail")
    def handle_mail_command(ack, body, respond, logger) -> None:
        ack()
        try:
            from .commands import dispatch

            text = (body.get("text") or "").strip()
            dispatch(text, respond)
        except Exception as exc:
            logger.exception(f"/mail command failed: {exc}")
            try:
                respond(text=f":warning: `/mail` failed: `{exc}`", response_type="ephemeral")
            except Exception:
                pass

    @app.event("message")
    def handle_dm(event, client, logger) -> None:
        """Free-text DM → same dispatch as `/mail`, with NL search fallback.

        Ignores: non-IM channels, messages from bots/self, edits/deletes, empty text."""
        if event.get("channel_type") != "im":
            return
        if event.get("subtype") in {"bot_message", "message_changed", "message_deleted"}:
            return
        if event.get("bot_id"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        channel = event["channel"]

        # `respond` analogue for DMs: chat_postMessage to the IM channel.
        # We strip `response_type` (only valid for slash response_url posts).
        def _respond(**kwargs) -> None:
            kwargs.pop("response_type", None)
            client.chat_postMessage(channel=channel, **kwargs)

        try:
            from .commands import dispatch

            dispatch(text, _respond, fallback_to_search=True)
        except Exception as exc:
            logger.exception(f"DM dispatch failed: {exc}")
            try:
                _respond(text=f":warning: Error: `{exc}`")
            except Exception:
                pass

    return app


def run_listener() -> None:
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise RuntimeError("SLACK_APP_TOKEN not set (xapp-…)")
    app = _build_app()
    print("[mail-agent] Slack listener started (Socket Mode). Ctrl-C to stop.")
    SocketModeHandler(app, app_token).start()

"""Auto-append a corrected example to eval/fixtures.yaml.

Single place for the side-effect; called by the modal submit handler.
"""

from __future__ import annotations

from pathlib import Path

from ..evaluation.dataset import LabeledExample
from ..evaluation.io import load, save
from ..evaluation.seed import _fetch_email
from ..gmail.accounts import load_accounts
from ..models import AccountName, Bucket, MessageId


def append_correction_to_fixtures(
    account_name: str,
    message_id: str,
    corrected_bucket: Bucket,
    note: str | None,
    fixtures_path: Path = Path("eval/fixtures.yaml"),
) -> bool:
    accounts = {a.name: a for a in load_accounts()}
    account = accounts.get(account_name)
    if account is None or not account.is_authorized:
        return False
    try:
        email = _fetch_email(account, MessageId(message_id))
    except Exception:
        return False
    if email is None:
        return False

    examples = load(fixtures_path)
    updated = False
    note_value = note or "Added via Slack correction"
    for ex in examples:
        if ex.email.id == message_id and ex.email.account == AccountName(account_name):
            ex.expected_bucket = corrected_bucket
            ex.expected_rule_name = None
            ex.notes = note_value
            updated = True
            break
    if not updated:
        examples.append(
            LabeledExample(
                email=email,
                expected_bucket=corrected_bucket,
                expected_rule_name=None,
                notes=note_value,
            )
        )
    save(fixtures_path, examples)
    return True

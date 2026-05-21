from __future__ import annotations

import json
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .accounts import Account

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _has_required_scopes(creds: Credentials) -> bool:
    granted = set(creds.scopes or [])
    return all(s in granted for s in SCOPES)


def token_days_remaining(account: Account) -> float | None:
    """Days until the stored token expires. None if no token or no expiry."""
    if not account.token_path.exists():
        return None
    try:
        data = json.loads(account.token_path.read_text())
    except Exception:
        return None
    expiry_raw = data.get("expiry")
    if not expiry_raw:
        return None
    try:
        # Google library stores expiry as ISO 8601, sometimes without tz.
        dt = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400
    except Exception:
        return None


def get_credentials(account: Account, interactive: bool = True) -> Credentials:
    """Return valid Credentials for the account. Runs browser auth on first use
    or when granted scopes don't cover SCOPES (e.g. after a scope upgrade)."""
    if not account.credentials_path.exists():
        raise FileNotFoundError(
            f"Missing client secret for account '{account.name}': "
            f"{account.credentials_path}. Download from Google Cloud Console "
            f"(APIs & Services → Credentials → OAuth client → Download JSON) "
            f"and place at this path."
        )

    creds: Credentials | None = None
    if account.token_path.exists():
        creds = Credentials.from_authorized_user_info(
            json.loads(account.token_path.read_text()),
            SCOPES,
        )
        if creds and not _has_required_scopes(creds):
            creds = None  # stored token predates scope upgrade; force re-auth

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            account.token_path.write_text(creds.to_json())
            account.token_path.chmod(0o600)  # refresh tokens are secrets
            return creds
        except Exception:
            creds = None  # fall through to fresh auth

    if not interactive:
        raise RuntimeError(
            f"Account '{account.name}' needs interactive auth "
            f"(possibly due to gmail.modify scope upgrade). "
            f"Run `mail-agent setup-gmail` first."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(account.credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    account.token_path.parent.mkdir(parents=True, exist_ok=True)
    account.token_path.write_text(creds.to_json())
    account.token_path.chmod(0o600)  # refresh tokens are secrets
    return creds

"""End-to-end sanity check. `mail-agent doctor` runs this — diagnoses
configuration / auth / scheduler / Slack health in one command."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Status = Literal["pass", "warn", "fail"]


class Check(BaseModel):
    name: str
    status: Status
    detail: str = ""


def _check_env_vars() -> list[Check]:
    checks: list[Check] = []
    required = {
        "ANTHROPIC_API_KEY": "Anthropic API key for LLM classification.",
        "GMAIL_ACCOUNTS": "Comma-separated Gmail account labels.",
    }
    optional = {
        "SLACK_BOT_TOKEN": "Slack bot token (skips Slack if unset).",
        "SLACK_APP_TOKEN": "Slack app token (needed for listener / slash commands).",
        "SLACK_USER_ID": "Slack member ID for DM delivery.",
    }
    for var, desc in required.items():
        if os.environ.get(var):
            checks.append(Check(name=f"env: {var}", status="pass"))
        else:
            checks.append(Check(name=f"env: {var}", status="fail", detail=desc))
    for var, desc in optional.items():
        if os.environ.get(var):
            checks.append(Check(name=f"env: {var}", status="pass"))
        else:
            checks.append(Check(name=f"env: {var}", status="warn", detail=f"unset — {desc}"))
    return checks


def _check_file_perms(path: Path) -> Check:
    if not path.exists():
        return Check(name=f"file: {path.name}", status="fail", detail=f"missing: {path}")
    mode = path.stat().st_mode & 0o777
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return Check(
            name=f"perms: {path.name}",
            status="warn",
            detail=f"mode {oct(mode)} — consider `chmod 600 {path}`",
        )
    return Check(name=f"perms: {path.name}", status="pass", detail=f"mode {oct(mode)}")


def _check_gmail_accounts() -> list[Check]:
    from .gmail.accounts import load_accounts
    from .gmail.auth import token_days_remaining

    checks: list[Check] = []
    accounts = load_accounts()
    if not accounts:
        checks.append(Check(name="gmail: accounts", status="fail", detail="GMAIL_ACCOUNTS empty"))
        return checks
    for acct in accounts:
        if not acct.credentials_path.exists():
            checks.append(
                Check(
                    name=f"gmail: {acct.name} credentials",
                    status="fail",
                    detail=f"missing {acct.credentials_path}",
                )
            )
            continue
        checks.append(_check_file_perms(acct.credentials_path))
        if not acct.is_authorized:
            checks.append(
                Check(
                    name=f"gmail: {acct.name} token",
                    status="warn",
                    detail="not authorized; run `mail-agent setup-gmail`",
                )
            )
            continue
        checks.append(_check_file_perms(acct.token_path))
        days = token_days_remaining(acct)
        if days is None:
            checks.append(
                Check(
                    name=f"gmail: {acct.name} expiry",
                    status="warn",
                    detail="cannot read token expiry",
                )
            )
        elif days < 2:
            checks.append(
                Check(
                    name=f"gmail: {acct.name} expiry",
                    status="warn",
                    detail=f"token expires in {days:.1f} days — run `setup-gmail` soon",
                )
            )
        else:
            checks.append(
                Check(
                    name=f"gmail: {acct.name} expiry",
                    status="pass",
                    detail=f"{days:.1f} days remaining",
                )
            )
    return checks


def _check_slack() -> Check:
    if not os.environ.get("SLACK_BOT_TOKEN"):
        return Check(name="slack: reachable", status="warn", detail="not configured")
    try:
        from .slack.client import get_client

        resp = get_client().auth_test()
        if resp.get("ok"):
            return Check(
                name="slack: reachable",
                status="pass",
                detail=f"team={resp.get('team')} user={resp.get('user')}",
            )
        return Check(name="slack: reachable", status="fail", detail=str(resp))
    except Exception as exc:
        return Check(name="slack: reachable", status="fail", detail=str(exc))


def _check_db() -> Check:
    from .store.sqlite import _db_path, init_db

    path = _db_path()
    try:
        init_db()
        return Check(name="db: writable", status="pass", detail=f"{path}")
    except Exception as exc:
        return Check(name="db: writable", status="fail", detail=str(exc))


def _check_schedule() -> list[Check]:
    """Status interpretation:
    - keep-alive jobs (listener) must be `running` to be healthy.
    - interval / daily jobs are idle between fires; `enabled` is enough.
    """
    try:
        from .schedule.detect import get_scheduler

        sched = get_scheduler()
        installed = sched.installed_jobs()
        if not installed:
            return [Check(name="schedule: installed", status="warn", detail="no jobs installed")]
        statuses = sched.status()
        out: list[Check] = []
        for s in statuses:
            is_listener = s.name.endswith("slack-listener")
            if is_listener:
                if s.enabled and s.running:
                    out.append(Check(name=f"schedule: {s.name}", status="pass", detail="running"))
                else:
                    out.append(
                        Check(
                            name=f"schedule: {s.name}",
                            status="warn",
                            detail="should be running but isn't",
                        )
                    )
            else:
                if s.enabled:
                    out.append(
                        Check(
                            name=f"schedule: {s.name}",
                            status="pass",
                            detail="enabled (idle between fires)",
                        )
                    )
                else:
                    out.append(Check(name=f"schedule: {s.name}", status="warn", detail="disabled"))
        return out
    except Exception as exc:
        return [Check(name="schedule: detect", status="warn", detail=str(exc))]


def _check_config() -> Check:
    try:
        from .config import load_config

        cfg = load_config("config/rules.yaml")
        return Check(
            name="config: rules.yaml",
            status="pass",
            detail=f"{len(cfg.rules)} rules · {len(cfg.llm.nl_rules)} NL rules · floor={cfg.llm.auto_mark_min_confidence}",
        )
    except Exception as exc:
        return Check(name="config: rules.yaml", status="fail", detail=str(exc))


def run_diagnostics() -> list[Check]:
    checks: list[Check] = []
    checks.extend(_check_env_vars())
    checks.append(_check_file_perms(Path(".env")))
    checks.append(_check_config())
    checks.append(_check_db())
    checks.extend(_check_gmail_accounts())
    checks.append(_check_slack())
    checks.extend(_check_schedule())
    return checks

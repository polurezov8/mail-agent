"""Tests for mail_agent.init_cmd.run_init()."""

from __future__ import annotations

import stat
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from mail_agent.init_cmd import run_init

ENV_EXAMPLE = """\
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_ACCOUNTS=personal
SLACK_BOT_TOKEN=              # xoxb-…
"""


@pytest.fixture()
def repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text(ENV_EXAMPLE)
    return tmp_path


def _null_console() -> Console:
    return Console(quiet=True)


# ── Step 1: .env ──────────────────────────────────────────────────────────────

def test_init_creates_env_from_example(repo_root, monkeypatch):
    # ANTHROPIC_API_KEY, then _prompt_account_names: count="1" + name="personal", then SLACK_BOT_TOKEN
    answers = iter(["sk-ant-test", "1", "personal", "xoxb-test"])
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: next(answers))
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: True)  # continue without creds

    code = run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    assert code == 0
    env_path = repo_root / ".env"
    assert env_path.exists()
    assert oct(stat.S_IMODE(env_path.stat().st_mode)) == "0o600"
    content = env_path.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-test" in content
    assert "GMAIL_ACCOUNTS=personal" in content
    assert "SLACK_BOT_TOKEN=xoxb-test" in content


def test_init_preserves_comments_in_env(repo_root, monkeypatch):
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: kw.get("default", ""))
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: False)

    run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    content = (repo_root / ".env").read_text()
    assert "# xoxb" not in content  # inline comment stripped from value
    assert "SLACK_BOT_TOKEN=" in content  # key preserved


def test_init_keeps_existing_env_and_fixes_perms(repo_root, monkeypatch):
    env_path = repo_root / ".env"
    env_path.write_text("ANTHROPIC_API_KEY=existing\n")
    env_path.chmod(0o644)

    prompt_called = []
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: prompt_called.append(1) or "")
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: True)  # continue without creds

    code = run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    assert code == 0
    assert not prompt_called, "Prompt.ask must not be called when .env already exists"
    assert env_path.read_text() == "ANTHROPIC_API_KEY=existing\n"
    assert oct(stat.S_IMODE(env_path.stat().st_mode)) == "0o600"


# ── Step 2: creds ─────────────────────────────────────────────────────────────

def test_init_chmods_creds_dir_and_existing_credentials(repo_root, monkeypatch):
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: kw.get("default", ""))
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: True)
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")

    creds_dir = repo_root / "creds"
    creds_dir.mkdir()
    cred_file = creds_dir / "personal_credentials.json"
    cred_file.write_text("{}")
    cred_file.chmod(0o644)

    run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    assert oct(stat.S_IMODE(creds_dir.stat().st_mode)) == "0o700"
    assert oct(stat.S_IMODE(cred_file.stat().st_mode)) == "0o600"


def test_init_returns_1_when_user_declines_missing_credentials(repo_root, monkeypatch):
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: kw.get("default", ""))
    # Confirm.ask for missing credentials returns False → abort
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: False)
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")

    code = run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    assert code == 1


# ── Step 3: Gmail OAuth ───────────────────────────────────────────────────────

def test_init_calls_get_credentials_for_each_account(repo_root, monkeypatch):
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: kw.get("default", ""))
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: True)
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")

    creds_dir = repo_root / "creds"
    creds_dir.mkdir()
    (creds_dir / "personal_credentials.json").write_text("{}")

    mock_get_creds = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("mail_agent.gmail.auth.get_credentials", mock_get_creds)

    code = run_init(skip_slack=True, skip_doctor=True, console=_null_console())

    assert code == 0
    mock_get_creds.assert_called_once()


def test_init_skip_gmail_flag_skips_oauth(repo_root, monkeypatch):
    monkeypatch.setattr("mail_agent.init_cmd.Prompt.ask", lambda *a, **kw: kw.get("default", ""))
    monkeypatch.setattr("mail_agent.init_cmd.Confirm.ask", lambda *a, **kw: True)
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")

    called = []
    monkeypatch.setattr(
        "mail_agent.gmail.auth.get_credentials",
        lambda *a, **kw: called.append(1),
    )

    run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    assert not called, "get_credentials must not be called when --skip-gmail is set"


# ── KeyboardInterrupt ─────────────────────────────────────────────────────────

def test_init_handles_keyboard_interrupt(repo_root, monkeypatch):
    monkeypatch.setattr(
        "mail_agent.init_cmd.Prompt.ask",
        lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    env_path = repo_root / ".env"
    code = run_init(skip_gmail=True, skip_slack=True, skip_doctor=True, console=_null_console())

    assert code == 130
    # Atomic write must not leave a partial .env
    assert not env_path.exists() or env_path.read_text() == ""

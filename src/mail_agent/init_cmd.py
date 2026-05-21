"""One-shot bootstrap wizard: .env, credentials permissions, Gmail OAuth, Slack ping, doctor."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

_SECRET_KEYS = {"ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"}
_GCP_CONSOLE_URL = "https://console.cloud.google.com/apis/credentials"


def _render_doctor_checks(checks: list, console: Console) -> int:
    counts: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0}
    for c in checks:
        counts[c.status] += 1
    t = Table(title="mail-agent doctor")
    t.add_column("Status", justify="center")
    t.add_column("Check")
    t.add_column("Detail", overflow="fold")
    icon = {"pass": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}
    for c in checks:
        t.add_row(icon[c.status], c.name, c.detail)
    console.print(t)
    console.print(
        f"\n[green]{counts['pass']} pass[/green] · "
        f"[yellow]{counts['warn']} warn[/yellow] · "
        f"[red]{counts['fail']} fail[/red]"
    )
    return counts["fail"]


_ACCOUNT_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_ACCOUNT_DEFAULTS = ["personal", "work", "side", "archive"]


def _prompt_account_names(console: Console) -> str:
    """Ask how many Gmail accounts and collect a valid name for each."""
    raw_n = Prompt.ask("  How many Gmail accounts?", default="1")
    try:
        count = max(1, int(raw_n))
    except ValueError:
        count = 1

    names: list[str] = []
    seen: set[str] = set()
    for i in range(count):
        default = _ACCOUNT_DEFAULTS[i] if i < len(_ACCOUNT_DEFAULTS) else f"account{i + 1}"
        while True:
            name = Prompt.ask(f"  Account {i + 1} name", default=default).strip().lower()
            if not _ACCOUNT_NAME_RE.match(name):
                console.print(
                    "  [yellow]Use only lowercase letters, digits, hyphens, or underscores.[/yellow]"
                )
                continue
            if name in seen:
                console.print("  [yellow]Duplicate name — choose a different one.[/yellow]")
                continue
            seen.add(name)
            names.append(name)
            console.print(f"  [dim]Creds expected at: creds/{name}_credentials.json[/dim]")
            break

    return ",".join(names)


def _step_env(
    env_path: Path,
    env_example_path: Path,
    console: Console,
) -> None:
    console.print("\n[bold]Step 1 — .env[/bold]")

    if env_path.exists():
        console.print(f"[dim]{env_path} already exists — fixing permissions.[/dim]")
        env_path.chmod(0o600)
        console.print("[green]✓ Permissions set to 600.[/green]")
        return

    if not env_example_path.exists():
        raise FileNotFoundError(
            f"{env_example_path} not found — run from the repo root."
        )

    lines = env_example_path.read_text().splitlines()
    out_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        if "=" not in line:
            out_lines.append(line)
            continue
        key, rest = line.split("=", 1)
        key = key.strip()
        default = rest.split("#")[0].strip()

        if key == "GMAIL_ACCOUNTS":
            value = _prompt_account_names(console)
        else:
            value = Prompt.ask(
                f"  [bold]{key}[/bold]",
                default=default,
                password=key in _SECRET_KEYS,
            )
        out_lines.append(f"{key}={value}")

    content = "\n".join(out_lines) + "\n"

    fd, tmp = tempfile.mkstemp(dir=env_path.parent, prefix=".env.tmp.")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.replace(tmp, env_path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(tmp).unlink(missing_ok=True)
        raise

    env_path.chmod(0o600)
    load_dotenv(str(env_path), override=True)
    console.print(f"[green]✓ {env_path} created (600).[/green]")


def _step_creds(creds_dir: Path, console: Console) -> bool:
    console.print("\n[bold]Step 2 — credentials directory[/bold]")
    creds_dir.mkdir(exist_ok=True)
    creds_dir.chmod(0o700)

    from .gmail.accounts import load_accounts

    accounts = load_accounts(creds_dir)
    for account in accounts:
        if account.credentials_path.exists():
            account.credentials_path.chmod(0o600)
            console.print(f"[green]✓ {account.credentials_path} (600).[/green]")
        else:
            console.print(
                f"[yellow]! {account.credentials_path} missing.[/yellow]\n"
                f"  Download OAuth Desktop client JSON:\n"
                f"  {_GCP_CONSOLE_URL}\n"
                f"  Save as [bold]{account.credentials_path}[/bold]"
            )
            if not Confirm.ask(
                f"  Continue without '{account.name}' credentials?", default=False
            ):
                return False
    return True


def _step_setup_gmail(console: Console) -> bool:
    console.print("\n[bold]Step 3 — Gmail OAuth[/bold]")

    from .gmail.accounts import load_accounts
    from .gmail.auth import get_credentials

    accounts = load_accounts()
    if not accounts:
        console.print("[yellow]! GMAIL_ACCOUNTS not set — skipping.[/yellow]")
        return True

    for account in accounts:
        if not account.credentials_path.exists():
            console.print(
                f"[yellow]! Skipping '{account.name}' — credentials missing.[/yellow]"
            )
            continue
        console.print(f"[bold]  Authorizing '{account.name}'…[/bold]")
        try:
            get_credentials(account, interactive=True)
            console.print(f"[green]✓ '{account.name}' authorized.[/green]")
        except Exception as exc:
            console.print(f"[red]✗ '{account.name}' failed: {exc}[/red]")
            console.print(
                "  Fix the error above and re-run `mail-agent init` or `mail-agent setup-gmail`."
            )
            return False
    return True


def _step_slack_test(console: Console) -> None:
    console.print("\n[bold]Step 4 — Slack ping[/bold]")

    from .slack.client import is_configured

    if not is_configured():
        console.print(
            "[yellow]! SLACK_BOT_TOKEN not set — skipping Slack test.[/yellow]\n"
            "  Set SLACK_BOT_TOKEN / SLACK_APP_TOKEN / SLACK_USER_ID in .env,\n"
            "  then re-run `mail-agent init --skip-gmail`."
        )
        return

    from .slack.client import get_channel_id, get_client

    try:
        client = get_client()
        channel = get_channel_id()
        client.chat_postMessage(channel=channel, text="✅ mail-agent connected to Slack.")
        console.print(f"[green]✓ Slack connected (channel {channel}).[/green]")
    except Exception as exc:
        console.print(
            f"[yellow]! Slack error: {exc}[/yellow]\n"
            "  `mail-agent doctor` will report the details."
        )


def _step_doctor(console: Console) -> None:
    console.print("\n[bold]Step 5 — doctor[/bold]")

    from .doctor import run_diagnostics

    checks = run_diagnostics()
    fail_count = _render_doctor_checks(checks, console)
    if fail_count:
        console.print(
            "[yellow]Fix the above and re-run `mail-agent doctor`.[/yellow]"
        )


def run_init(
    *,
    env_path: Path = Path(".env"),
    env_example_path: Path = Path(".env.example"),
    creds_dir: Path = Path("creds"),
    skip_gmail: bool = False,
    skip_slack: bool = False,
    skip_doctor: bool = False,
    console: Console | None = None,
) -> int:
    """Run all bootstrap steps. Returns 0 on success, 1 on fatal error, 130 on Ctrl-C."""
    if console is None:
        console = Console()

    console.print(
        Panel(
            "One-shot bootstrap. Ctrl-C any time — completed steps are durable.",
            title="mail-agent init",
            border_style="cyan",
        )
    )

    try:
        _step_env(env_path, env_example_path, console)

        if not _step_creds(creds_dir, console):
            return 1

        if not skip_gmail:
            if not _step_setup_gmail(console):
                return 1

        if not skip_slack:
            _step_slack_test(console)

        if not skip_doctor:
            _step_doctor(console)

        console.print(
            Panel(
                "\n".join(
                    [
                        "[bold]You're set. Next steps:[/bold]",
                        "",
                        "  [cyan]uv run mail-agent triage --mock[/cyan]      — see the pipeline",
                        "  [cyan]uv run mail-agent rules wizard[/cyan]        — author your rules",
                        "  [cyan]uv run mail-agent schedule install[/cyan]    — run unattended",
                    ]
                ),
                border_style="green",
                title="mail-agent init complete",
            )
        )
        return 0

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted. Re-run `mail-agent init` to resume — completed steps are durable.[/yellow]"
        )
        return 130

from __future__ import annotations

import typer
from dotenv import load_dotenv
from rich.console import Console

from .config import load_config
from .graph.build import build_graph

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def _root() -> None:
    """Personal Gmail triage agent."""


def _resolve_accounts(filter_names: list[str] | None) -> list[str] | None:
    """Validate account names against configured accounts. Returns None (all) or the filtered list."""
    if not filter_names:
        return None
    from .gmail.accounts import load_accounts

    known = {a.name for a in load_accounts()}
    unknown = [n for n in filter_names if n not in known]
    if unknown:
        console.print(
            f"[red]Unknown account(s): {', '.join(unknown)}. "
            f"Configured: {', '.join(sorted(known)) or '(none)'}[/red]"
        )
        raise typer.Exit(code=1)
    return filter_names


@app.command()
def triage(
    mock: bool = typer.Option(False, "--mock", help="Use hardcoded mock inbox instead of Gmail."),
    config_path: str = typer.Option("config/rules.yaml", "--config", help="Path to rules.yaml."),
    query: str = typer.Option(
        None, "--query", help="Override Gmail search query (default: is:unread in:inbox)."
    ),
    limit: int = typer.Option(None, "--limit", help="Max messages per account."),
    reprocess: bool = typer.Option(
        False, "--reprocess", help="Ignore processed-IDs store; re-classify everything fetched."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show would-mark-read decisions without modifying Gmail."
    ),
    no_slack: bool = typer.Option(False, "--no-slack", help="Skip Slack delivery for this run."),
    account: list[str] | None = typer.Option(
        None, "--account", help="Limit to specific account(s). Repeatable: --account work."
    ),
) -> None:
    """Run one triage pass."""
    load_dotenv()
    cfg = load_config(config_path)
    graph = build_graph()
    graph.invoke(
        {
            "config": cfg,
            "mock": mock,
            "query": query,
            "limit": limit,
            "skip_processed": not reprocess,
            "dry_run": dry_run,
            "no_slack": no_slack,
            "accounts": _resolve_accounts(account),
        }
    )


@app.command()
def init(
    skip_gmail: bool = typer.Option(False, "--skip-gmail", help="Skip Gmail OAuth step."),
    skip_slack: bool = typer.Option(False, "--skip-slack", help="Skip Slack test step."),
    skip_doctor: bool = typer.Option(False, "--skip-doctor", help="Skip doctor step."),
) -> None:
    """One-shot bootstrap: .env, creds perms, Gmail OAuth, Slack ping, doctor."""
    from .init_cmd import run_init

    raise typer.Exit(code=run_init(skip_gmail=skip_gmail, skip_slack=skip_slack, skip_doctor=skip_doctor))


@app.command("setup-gmail")
def setup_gmail() -> None:
    """Run interactive OAuth flow for each configured Gmail account."""
    load_dotenv()
    from .gmail.accounts import load_accounts
    from .gmail.auth import get_credentials

    accounts = load_accounts()
    if not accounts:
        console.print("[red]GMAIL_ACCOUNTS is empty. Edit .env.[/red]")
        raise typer.Exit(code=1)

    for account in accounts:
        console.print(f"[bold]Authorizing account '{account.name}'…[/bold]")
        if not account.credentials_path.exists():
            console.print(
                f"[red]Missing {account.credentials_path}. "
                f"Download client secret JSON from Google Cloud Console.[/red]"
            )
            raise typer.Exit(code=1)
        get_credentials(account, interactive=True)
        console.print(f"[green]✓ '{account.name}' authorized.[/green]")
    console.print("[green]All accounts ready. Run: mail-agent triage[/green]")


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural-language query."),
    limit: int = typer.Option(10, "--limit", help="Max results."),
    post_to_slack: bool = typer.Option(
        False, "--post-to-slack", help="Also post results to your Slack DM."
    ),
    account: list[str] | None = typer.Option(
        None, "--account", help="Limit to specific account(s). Repeatable."
    ),
) -> None:
    """NL search across your authorized Gmail accounts (read-only)."""
    load_dotenv()
    from rich.table import Table

    from .config import load_config
    from .gmail.accounts import load_accounts
    from .gmail.client import search_messages
    from .search import build_gmail_query

    cfg = load_config("config/rules.yaml")
    plan = build_gmail_query(query, cfg.llm)
    if not plan.gmail_query:
        console.print("[yellow]Empty query plan.[/yellow]")
        raise typer.Exit(code=1)

    effective_limit = plan.suggested_limit or limit

    console.print(f"[dim]Gmail query:[/dim] [bold]{plan.gmail_query}[/bold]")
    console.print(f"[dim]Why:[/dim] {plan.reasoning}")
    if plan.suggested_limit:
        console.print(f"[dim]Limit:[/dim] {plan.suggested_limit} (from your query)")

    account_filter = _resolve_accounts(account)
    hits = []
    for acct in load_accounts():
        if account_filter is not None and acct.name not in account_filter:
            continue
        if acct.is_authorized:
            hits.extend(search_messages(acct, plan.gmail_query, limit=effective_limit))
    hits.sort(key=lambda m: m.received_at, reverse=True)
    hits = hits[:effective_limit]

    if not hits:
        console.print("[dim]No matches.[/dim]")
        return

    t = Table(title=f"Search · {query[:60]}")
    t.add_column("When")
    t.add_column("From", overflow="fold")
    t.add_column("Subject", overflow="fold")
    for h in hits:
        t.add_row(
            h.received_at.strftime("%Y-%m-%d %H:%M"),
            h.from_email,
            h.subject[:80],
        )
    console.print(t)

    if post_to_slack:
        from .slack.blocks import search_results_blocks
        from .slack.client import get_channel_id, get_client, is_configured

        if not is_configured():
            console.print("[yellow]Slack not configured; skipping post.[/yellow]")
            return
        client = get_client()
        channel = get_channel_id()
        client.chat_postMessage(
            channel=channel,
            blocks=search_results_blocks(query, plan.gmail_query, hits, plan.reasoning),
            text=f"Search · {query[:80]}",
            unfurl_links=False,
            unfurl_media=False,
        )
        console.print("[green]Posted to Slack.[/green]")


@app.command()
def unsubscribe(
    hours: int = typer.Option(168, "--hours", help="Look back window (default 7d)."),
    limit: int = typer.Option(20, "--limit", help="Max senders to attempt."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Actually perform unsubscribe (default: dry-run)."
    ),
) -> None:
    """Bulk-unsubscribe from senders we auto-marked as ignore.

    Dry-run by default — shows candidates + which support one-click POST.
    Pass --confirm to actually execute. Only RFC 8058 one-click POST is
    auto-executed; mailto unsubscribes are reported but not sent.
    """
    load_dotenv()
    from rich.table import Table

    from .gmail.accounts import load_accounts
    from .gmail.auth import get_credentials
    from .gmail.unsubscribe import parse_list_unsubscribe, perform_unsubscribe
    from .store.sqlite import (
        init_db,
        list_unsubscribe_candidates,
        log_unsubscribe,
    )

    init_db()
    candidates = list_unsubscribe_candidates(hours=hours, limit=limit)
    if not candidates:
        console.print(
            f"[dim]No unsubscribe candidates from the last {hours}h "
            f"(or all already unsubscribed).[/dim]"
        )
        return

    # Resolve a Gmail service to look up sample message headers.
    accounts = load_accounts()
    if not accounts:
        console.print("[red]No Gmail accounts configured.[/red]")
        raise typer.Exit(code=1)

    from googleapiclient.discovery import build

    services = {}
    for a in accounts:
        if not a.is_authorized:
            continue
        creds = get_credentials(a, interactive=False)
        services[a.name] = build("gmail", "v1", credentials=creds, cache_discovery=False)

    t = Table(title=f"Unsubscribe candidates (last {hours}h)")
    t.add_column("Sender", overflow="fold")
    t.add_column("Hits", justify="right")
    t.add_column("Method")
    t.add_column("Result" if confirm else "Would do")

    for c in candidates:
        sender = c["from_email"]
        sample_id = c["sample_message_id"]

        option = None
        for svc in services.values():
            try:
                raw = (
                    svc.users()
                    .messages()
                    .get(
                        userId="me",
                        id=sample_id,
                        format="metadata",
                        metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post"],
                    )
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in raw.get("payload", {}).get("headers", [])}
                option = parse_list_unsubscribe(headers)
                break
            except Exception:
                continue

        if option is None or option.method == "none":
            t.add_row(sender, str(c["hits"]), "—", "skip (no header)")
            continue

        if not confirm:
            target = option.url or option.mailto or "?"
            t.add_row(sender, str(c["hits"]), option.method, f"would POST/SEND → {target[:60]}")
            continue

        result = perform_unsubscribe(sender, option)
        log_unsubscribe(sender, result.method, result.success, result.detail)
        marker = "[green]✓[/green]" if result.success else "[yellow]![/yellow]"
        t.add_row(sender, str(c["hits"]), option.method, f"{marker} {result.detail}")

    console.print(t)
    if not confirm:
        console.print("\n[dim]Dry-run. Re-run with [bold]--confirm[/bold] to execute.[/dim]")


@app.command()
def stats(
    period: str = typer.Option(
        "week", "--period", help="day | week | month | all"
    ),
    post_to_slack: bool = typer.Option(
        False, "--post-to-slack", help="Also post to your Slack DM."
    ),
    account: list[str] | None = typer.Option(
        None, "--account", help="Limit to specific account(s). Repeatable."
    ),
) -> None:
    """Show triage metrics: counts, top rules, top senders, daily activity."""
    load_dotenv()
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from .store.stats import compute_metrics
    from .visualize import hbar, percent, sparkline

    s = compute_metrics(period=period, accounts=_resolve_accounts(account))
    period_label = {
        "day": "24 hours",
        "week": "7 days",
        "month": "30 days",
        "all": "all-time",
    }.get(s.period, s.period)

    # Hero
    hero = Text()
    hero.append(f"\n  {s.total_processed:>5}\n", style="bold white on cyan")
    hero.append("  mails processed\n", style="dim")

    # Buckets
    bucket_table = Table(show_header=False, box=None, padding=(0, 1))
    bucket_table.add_column("Bucket", style="bold")
    bucket_table.add_column("Bar", style="cyan")
    bucket_table.add_column("Count", justify="right")
    bucket_table.add_column("%", justify="right", style="dim")
    for b in ("respond", "notify", "ignore"):
        c = s.bucket_counts.get(b, 0)
        bucket_table.add_row(
            b, hbar(c, s.total_processed or 1, 24), str(c), percent(c, s.total_processed)
        )

    # Top rules
    rules_table = Table(show_header=False, box=None, padding=(0, 1))
    rules_table.add_column("Rule")
    rules_table.add_column("Bar", style="green")
    rules_table.add_column("Count", justify="right")
    if s.rule_top:
        rule_max = s.rule_top[0].count
        for r in s.rule_top[:8]:
            rules_table.add_row(r.name[:30], hbar(r.count, rule_max, 18), str(r.count))

    # Daily sparkline
    spark_line = ""
    if s.daily:
        values = [d.total for d in s.daily]
        spark_line = (
            f"  {sparkline(values)}\n  {s.daily[0].date}  →  {s.daily[-1].date}    "
            f"peak: {max(values)}"
        )

    footer = []
    if s.marked_read:
        footer.append(f"[green]{s.marked_read}[/green] marked read")
    if s.corrections:
        footer.append(f"[yellow]{s.corrections}[/yellow] corrections")
    if s.uncertain_band:
        footer.append(f"[red]{s.uncertain_band}[/red] uncertain")

    body = Group(
        hero,
        Text("By bucket", style="bold"),
        bucket_table,
        Text(""),
        Text("Top rules", style="bold") if s.rule_top else Text(""),
        rules_table if s.rule_top else Text(""),
        Text(""),
        Text("Daily activity", style="bold") if spark_line else Text(""),
        Text(spark_line, style="cyan") if spark_line else Text(""),
        Text(""),
        Text(" · ".join(footer)) if footer else Text(""),
    )
    console.print(Panel.fit(body, title=f"Mail Stats · {period_label}", border_style="cyan"))

    if post_to_slack:
        from .slack.blocks import stats_blocks
        from .slack.client import get_channel_id, get_client, is_configured

        if not is_configured():
            console.print("[yellow]Slack not configured; skipping post.[/yellow]")
            return
        client = get_client()
        channel = get_channel_id()
        client.chat_postMessage(
            channel=channel,
            blocks=stats_blocks(s),
            text=f"Mail Stats · {period_label}",
            unfurl_links=False,
            unfurl_media=False,
        )
        console.print("[green]Posted to Slack.[/green]")


@app.command()
def brief(
    hours: int = typer.Option(24, "--hours", help="Window size in hours."),
    post_to_slack: bool = typer.Option(
        False, "--post-to-slack", help="Also post to your Slack DM."
    ),
    account: list[str] | None = typer.Option(
        None, "--account", help="Limit to specific account(s). Repeatable."
    ),
) -> None:
    """Print (and optionally post) a daily activity Brief."""
    load_dotenv()
    from rich.panel import Panel

    from .brief import build_brief

    summary = build_brief(hours=hours, accounts=_resolve_accounts(account))
    bucket_line = " · ".join(
        f"{b}: {summary.bucket_counts.get(b, 0)}" for b in ("ignore", "notify", "respond")
    )
    lines = [
        f"[bold]Mail Brief · last {hours}h[/bold]",
        f"Processed: {summary.processed_total}   Marked read: {summary.marked_read}",
        f"Buckets: {bucket_line}",
    ]
    if summary.rule_counts:
        lines.append("\n[bold]Top rule hits[/bold]")
        for name, count in summary.rule_counts[:10]:
            lines.append(f"  • {name} × {count}")
    if summary.notable_respond:
        lines.append("\n[bold]Respond surfaced[/bold]")
        for it in summary.notable_respond:
            lines.append(f"  • {it.subject[:80]} — {it.from_email}")
    if summary.notable_notify:
        lines.append("\n[bold]Notify surfaced[/bold]")
        for it in summary.notable_notify:
            lines.append(f"  • {it.subject[:80]} — {it.from_email}")
    if summary.uncertain_band:
        lines.append(
            f"\n[dim]{summary.uncertain_band} uncertain auto-marks (run `mail-agent eval` style review)[/dim]"
        )
    console.print(Panel.fit("\n".join(lines), border_style="cyan"))

    if post_to_slack:
        from .slack.blocks import brief_blocks
        from .slack.client import get_channel_id, get_client, is_configured

        if not is_configured():
            console.print("[yellow]Slack not configured; skipping post.[/yellow]")
            return
        client = get_client()
        channel = get_channel_id()
        client.chat_postMessage(
            channel=channel,
            blocks=brief_blocks(summary),
            text=f"Mail Brief · last {hours}h",
            unfurl_links=False,
            unfurl_media=False,
        )
        console.print("[green]Posted to Slack.[/green]")


@app.command()
def doctor() -> None:
    """Sanity check: env vars, files, perms, auth, schedule, Slack, DB."""
    load_dotenv()
    from rich.table import Table

    from .doctor import run_diagnostics

    checks = run_diagnostics()
    counts = {"pass": 0, "warn": 0, "fail": 0}
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
    if counts["fail"]:
        raise typer.Exit(code=1)


@app.command("list-accounts")
def list_accounts() -> None:
    """Show configured Gmail accounts and their auth status."""
    load_dotenv()
    from .gmail.accounts import load_accounts

    accounts = load_accounts()
    if not accounts:
        console.print("[dim]No accounts configured.[/dim]")
        return
    for a in accounts:
        status = "[green]authorized[/green]" if a.is_authorized else "[yellow]needs setup[/yellow]"
        console.print(f"  {a.name:<12} {status}  creds={a.credentials_path}")


rules_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Manage triage rules.")
app.add_typer(rules_app, name="rules")


@rules_app.command("wizard")
def rules_wizard(
    config_path: str = typer.Option("config/rules.yaml", "--config", help="Path to rules.yaml."),
) -> None:
    """Interactive form. Walks through VIP, notify, ignore, NL rules, risk tuning."""
    from pathlib import Path

    from .wizard import run_wizard

    run_wizard(Path(config_path))


@rules_app.command("add")
def rules_add(
    text: str = typer.Argument(..., help="The NL rule, in quotes if it has spaces."),
    config_path: str = typer.Option("config/rules.yaml", "--config", help="Path to rules.yaml."),
) -> None:
    """Append a natural-language rule to llm.nl_rules in rules.yaml."""
    from pathlib import Path

    from .rules.edit import add_nl_rule

    added, total = add_nl_rule(text, Path(config_path))
    if added:
        console.print(f"[green]Added NL rule[/green] · {total} total")
        console.print(f"  • {text}")
    else:
        console.print(f"[yellow]Rule already exists ({total} total NL rules).[/yellow]")


@rules_app.command("remove")
def rules_remove(
    index: int = typer.Argument(..., help="1-based index from `rules show`."),
    config_path: str = typer.Option("config/rules.yaml", "--config", help="Path to rules.yaml."),
) -> None:
    """Remove an NL rule by index (1-based)."""
    from pathlib import Path

    from .rules.edit import remove_nl_rule

    removed, text = remove_nl_rule(index, Path(config_path))
    if removed:
        console.print(f"[green]Removed:[/green] {text}")
    else:
        console.print(f"[yellow]No NL rule at index {index}.[/yellow]")
        raise typer.Exit(code=1)


@rules_app.command("show")
def rules_show(
    config_path: str = typer.Option("config/rules.yaml", "--config", help="Path to rules.yaml."),
) -> None:
    """Print current rules in compact form."""
    from rich.table import Table

    cfg = load_config(config_path)
    t = Table(title="Rules")
    t.add_column("Name")
    t.add_column("Bucket")
    t.add_column("Auto-mark", justify="center")
    t.add_column("Description", overflow="fold")
    for r in cfg.rules:
        t.add_row(r.name, r.bucket.value, "✓" if r.auto_mark_read else "—", r.description)
    console.print(t)
    if cfg.llm.nl_rules:
        console.print("\n[bold]Natural-language rules[/bold] (LLM-only):")
        for r in cfg.llm.nl_rules:
            console.print(f"  • {r}")
    console.print(
        f"\n[dim]auto_mark_min_confidence = {cfg.llm.auto_mark_min_confidence}, "
        f"confidence_threshold = {cfg.llm.confidence_threshold}[/dim]"
    )


corrections_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="View / undo user corrections.",
)
app.add_typer(corrections_app, name="corrections")


@corrections_app.command("list")
def corrections_list(
    limit: int = typer.Option(20, "--limit", help="Max entries to show."),
) -> None:
    """List recent corrections (newest first)."""
    load_dotenv()
    from rich.table import Table

    from .store.sqlite import init_db, list_corrections

    init_db()
    rows = list_corrections(limit=limit)
    if not rows:
        console.print("[dim]No corrections yet.[/dim]")
        return
    t = Table(title=f"Corrections (latest {len(rows)})")
    t.add_column("ID", justify="right")
    t.add_column("When")
    t.add_column("From", overflow="fold")
    t.add_column("Subject", overflow="fold")
    t.add_column("Was → now")
    t.add_column("Sender-wide", justify="center")
    for r in rows:
        t.add_row(
            str(r["id"]),
            r["corrected_at"][:19],
            r.get("from_email") or "-",
            (r.get("subject") or "")[:60],
            f"{r['original_bucket']} → {r['corrected_bucket']}",
            "✓" if r["apply_to_sender"] else "—",
        )
    console.print(t)


@corrections_app.command("undo")
def corrections_undo(
    correction_id: int = typer.Argument(..., help="Correction ID (from `corrections list`)."),
) -> None:
    """Soft-undo a correction. Stops it overriding future mail from that
    sender; audit row preserved."""
    load_dotenv()
    from .store.sqlite import deactivate_correction, init_db

    init_db()
    if deactivate_correction(correction_id):
        console.print(f"[green]Correction #{correction_id} deactivated.[/green]")
    else:
        console.print(f"[yellow]No correction #{correction_id} found.[/yellow]")
        raise typer.Exit(code=1)


slack_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Slack integration.")
app.add_typer(slack_app, name="slack")


@slack_app.command("listen")
def slack_listen() -> None:
    """Run the Socket Mode listener for button clicks (Mark read, etc.).

    Blocks. Keep running in a separate terminal or under launchd.
    """
    load_dotenv()
    from .slack.listener import run_listener

    run_listener()


@slack_app.command("test")
def slack_test() -> None:
    """Send a test message to verify Slack config."""
    load_dotenv()
    from .slack.client import get_channel_id, get_client, is_configured

    if not is_configured():
        console.print("[red]SLACK_BOT_TOKEN not set in .env[/red]")
        raise typer.Exit(code=1)
    client = get_client()
    channel = get_channel_id()
    client.chat_postMessage(channel=channel, text="✅ mail-agent connected to Slack.")
    console.print(f"[green]Posted to channel {channel}[/green]")


schedule_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Background scheduler (launchd on macOS, systemd on Linux).",
)
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("install")
def schedule_install(
    poll_minutes: int = typer.Option(30, "--poll-minutes", help="Interval between triage runs."),
    daily_hour: int = typer.Option(
        9, "--daily-hour", help="Hour (0-23, local) for the daily catch-all run."
    ),
    daily_minute: int = typer.Option(0, "--daily-minute", help="Minute (0-59) for the daily run."),
    no_listener: bool = typer.Option(
        False, "--no-listener", help="Skip installing the Slack listener job."
    ),
) -> None:
    """Install scheduled jobs. Detects platform (launchd or systemd)."""
    load_dotenv()
    from .schedule.detect import detect_project_root, detect_uv, get_scheduler
    from .schedule.jobs import default_jobs

    sched = get_scheduler()
    project_root = detect_project_root()
    uv_bin = detect_uv()
    jobs = default_jobs(
        project_root=project_root,
        uv_bin=uv_bin,
        poll_interval_seconds=poll_minutes * 60,
        daily_hour=daily_hour,
        daily_minute=daily_minute,
        include_listener=not no_listener,
    )
    sched.install(jobs)
    console.print(f"[green]Installed {len(jobs)} jobs via {sched.name}.[/green]")
    for j in jobs:
        console.print(f"  • {j.name} ({j.schedule.kind})")
    console.print(f"[dim]Logs: {project_root / 'logs'}/[/dim]")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    """Remove all mail-agent scheduled jobs."""
    load_dotenv()
    from .schedule.detect import get_scheduler

    sched = get_scheduler()
    removed = sched.uninstall()
    if not removed:
        console.print("[dim]No jobs to remove.[/dim]")
        return
    console.print(f"[green]Removed {len(removed)} jobs via {sched.name}:[/green]")
    for n in removed:
        console.print(f"  • {n}")


@schedule_app.command("restart")
def schedule_restart(
    name: str = typer.Argument(
        None, help="Job name to restart (e.g. mail-agent.slack-listener). Omit = restart all."
    ),
) -> None:
    """Restart one or all installed jobs. Pick up new code after a `git pull`."""
    load_dotenv()
    from .schedule.detect import get_scheduler

    sched = get_scheduler()
    restarted = sched.restart(only=name)
    if not restarted:
        console.print(
            "[yellow]Nothing restarted (check job names with `schedule status`).[/yellow]"
        )
        raise typer.Exit(code=1)
    console.print(f"[green]Restarted {len(restarted)} job(s):[/green]")
    for n in restarted:
        console.print(f"  • {n}")


@schedule_app.command("status")
def schedule_status() -> None:
    """Show installed jobs and their state."""
    load_dotenv()
    from rich.table import Table

    from .schedule.detect import get_scheduler

    sched = get_scheduler()
    statuses = sched.status()
    if not statuses:
        console.print("[dim]No jobs installed.[/dim]")
        return
    t = Table(title=f"Scheduled jobs ({sched.name})")
    t.add_column("Name")
    t.add_column("Enabled", justify="center")
    t.add_column("Running", justify="center")
    t.add_column("Last exit", justify="right")
    for s in statuses:
        t.add_row(
            s.name,
            "✓" if s.enabled else "—",
            "✓" if s.running else "—",
            str(s.last_exit_code) if s.last_exit_code is not None else "-",
        )
    console.print(t)


eval_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Evaluation harness.")
app.add_typer(eval_app, name="eval")

_DEFAULT_FIXTURES = "eval/fixtures.yaml"


@eval_app.command("seed")
def eval_seed(
    account: str = typer.Option("personal", "--account", help="Gmail account to seed from."),
    limit: int = typer.Option(20, "--limit", help="Max examples to pull from processed_messages."),
    fixtures: str = typer.Option(_DEFAULT_FIXTURES, "--fixtures", help="Output YAML path."),
    append: bool = typer.Option(False, "--append", help="Append to existing fixtures (skip dups)."),
) -> None:
    """Bootstrap fixtures from recent processed mails.

    Sets expected_bucket / expected_rule_name to the agent's prior verdict.
    Review the YAML and correct any mislabels before running eval.
    """
    load_dotenv()
    from pathlib import Path

    from .evaluation.io import load, save
    from .evaluation.seed import seed_from_processed

    new_examples = seed_from_processed(account, limit)
    path = Path(fixtures)
    if append:
        existing = load(path)
        existing_ids = {ex.email.id for ex in existing}
        merged = list(existing) + [ex for ex in new_examples if ex.email.id not in existing_ids]
    else:
        merged = new_examples
    save(path, merged)
    console.print(
        f"[green]Wrote {len(merged)} examples → {path}[/green] ({len(new_examples)} new from seed)"
    )
    console.print(
        "[dim]Review the YAML and fix any wrong labels before `mail-agent eval run`.[/dim]"
    )


@eval_app.command("show")
def eval_show(
    fixtures: str = typer.Option(_DEFAULT_FIXTURES, "--fixtures", help="Fixtures YAML path."),
) -> None:
    """Print current labeled fixtures."""
    from pathlib import Path

    from rich.table import Table

    from .evaluation.io import load

    examples = load(Path(fixtures))
    if not examples:
        console.print(f"[dim]No examples at {fixtures}.[/dim]")
        return
    t = Table(title=f"Fixtures ({len(examples)})")
    t.add_column("From", overflow="fold")
    t.add_column("Subject", overflow="fold")
    t.add_column("Expected bucket")
    t.add_column("Expected rule")
    t.add_column("Notes", overflow="fold")
    for ex in examples:
        t.add_row(
            ex.email.from_email,
            ex.email.subject,
            ex.expected_bucket.value,
            ex.expected_rule_name or "-",
            ex.notes or "",
        )
    console.print(t)


@eval_app.command("run")
def eval_run(
    fixtures: str = typer.Option(_DEFAULT_FIXTURES, "--fixtures", help="Fixtures YAML path."),
    config_path: str = typer.Option("config/rules.yaml", "--config", help="Rules config path."),
    min_bucket_accuracy: float = typer.Option(
        0.85, "--min-bucket-accuracy", help="Exit non-zero if bucket accuracy below this."
    ),
    personal_only: bool = typer.Option(
        False,
        "--personal-only",
        help="Skip the public synthetic fixtures (default: merge personal + public).",
    ),
) -> None:
    """Evaluate the classifier against labeled fixtures.

    By default merges `eval/fixtures.yaml` (gitignored, personal mail) and
    `eval/fixtures.public.yaml` (committable, synthetic). Use --personal-only
    to score against your real mail alone.
    """
    load_dotenv()
    from pathlib import Path

    from .evaluation.io import load, load_all
    from .evaluation.report import print_report
    from .evaluation.runner import evaluate

    path = Path(fixtures)
    examples = load(path) if personal_only else load_all(path)
    if not examples:
        console.print(
            f"[yellow]No fixtures at {fixtures} (or {path.with_name('fixtures.public.yaml')}).[/yellow]"
        )
        raise typer.Exit(code=1)

    cfg = load_config(config_path)
    report = evaluate(cfg, examples)
    print_report(report, console)
    if report.bucket_accuracy < min_bucket_accuracy:
        console.print(
            f"[red]FAIL: bucket accuracy {report.bucket_accuracy * 100:.1f}% "
            f"below threshold {min_bucket_accuracy * 100:.0f}%.[/red]"
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()

"""Interactive CLI to build user's triage rules.

Walks through VIP, notify, ignore, NL rules, and tuning preferences,
then merges into config/rules.yaml (preserving existing rules)."""

from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

console = Console()


def _list_prompt(label: str, examples: list[str], hint: str = "blank to finish") -> list[str]:
    console.print(f"\n[bold cyan]{label}[/bold cyan]  [dim]({hint})[/dim]")
    for ex in examples:
        console.print(f"  [dim]e.g. {ex}[/dim]")
    items: list[str] = []
    n = 1
    while True:
        v = Prompt.ask(f"  {n}.", default="", show_default=False).strip()
        if not v:
            break
        items.append(v)
        n += 1
    return items


def _normalize_domain(d: str) -> str:
    return d.lstrip("@").lower()


def _build_rules_block(
    vip_emails: list[str],
    vip_domains: list[str],
    notify_emails: list[str],
    notify_subjects: list[str],
    ignore_emails: list[str],
    ignore_domains: list[str],
    ignore_subjects: list[str],
) -> list[dict]:
    """Build new rule dicts. VIP first (highest priority)."""
    new: list[dict] = []

    # RuleMatch combines populated fields with AND. Wizard collects emails
    # AND/OR domains independently, so emit a separate rule per list to get
    # the OR semantics users actually expect.
    if vip_emails:
        new.append(
            {
                "name": "vip_emails",
                "description": "VIP individual senders — always surface for response",
                "match": {"from_email": vip_emails},
                "bucket": "respond",
                "auto_mark_read": False,
            }
        )
    if vip_domains:
        new.append(
            {
                "name": "vip_domains",
                "description": "VIP sender domains — always surface for response",
                "match": {"from_domain": [_normalize_domain(d) for d in vip_domains]},
                "bucket": "respond",
                "auto_mark_read": False,
            }
        )

    if notify_emails:
        new.append(
            {
                "name": "notify_senders",
                "description": "Senders to surface in notify digest",
                "match": {"from_email": notify_emails},
                "bucket": "notify",
                "auto_mark_read": False,
            }
        )
    if notify_subjects:
        new.append(
            {
                "name": "notify_subjects",
                "description": "Subject keywords to notify",
                "match": {"subject_contains": notify_subjects},
                "bucket": "notify",
                "auto_mark_read": False,
            }
        )

    if ignore_emails:
        new.append(
            {
                "name": "ignore_emails",
                "description": "Individual senders to auto-mark-read as ignore",
                "match": {"from_email": ignore_emails},
                "bucket": "ignore",
                "auto_mark_read": True,
            }
        )
    if ignore_domains:
        new.append(
            {
                "name": "ignore_domains",
                "description": "Sender domains to auto-mark-read as ignore",
                "match": {"from_domain": [_normalize_domain(d) for d in ignore_domains]},
                "bucket": "ignore",
                "auto_mark_read": True,
            }
        )
    if ignore_subjects:
        new.append(
            {
                "name": "ignore_subjects",
                "description": "Subject keywords to auto-mark-read",
                "match": {"subject_contains": ignore_subjects},
                "bucket": "ignore",
                "auto_mark_read": True,
            }
        )

    return new


def _merge_yaml(
    rules_path: Path,
    new_rules: list[dict],
    nl_rules: list[str],
    auto_mark_min_confidence: float,
) -> dict:
    raw = yaml.safe_load(rules_path.read_text()) if rules_path.exists() else {}
    existing_rules = raw.get("rules", [])
    llm = raw.get("llm", {})

    # New user rules go FIRST so VIP/specific patterns match before generic ones.
    new_names = {r["name"] for r in new_rules}
    preserved = [r for r in existing_rules if r["name"] not in new_names]
    merged_rules = new_rules + preserved

    llm["auto_mark_min_confidence"] = auto_mark_min_confidence
    # Append unique nl_rules (preserve existing, add new)
    existing_nl = llm.get("nl_rules") or []
    combined_nl: list[str] = list(existing_nl)
    for r in nl_rules:
        if r not in combined_nl:
            combined_nl.append(r)
    llm["nl_rules"] = combined_nl

    raw["rules"] = merged_rules
    raw["llm"] = llm
    return raw


def run_wizard(rules_path: Path) -> None:
    console.print(
        Panel.fit(
            "Mail triage rules wizard\nConcrete examples preferred. Blank line ends each section.",
            style="bold cyan",
        )
    )

    # 1. VIP
    console.print(
        "\n[bold]1. VIP allowlist[/bold] — these go to [yellow]respond[/yellow] bucket. "
        "Never auto-marked, always surfaced realtime."
    )
    vip_emails = _list_prompt("VIP individual emails", ["jane@partner.com", "ceo@yourco.test"])
    vip_domains = _list_prompt(
        "VIP domains (whole orgs)", ["@yourcompany.com", "@board-investor.com"]
    )

    # 2. Notify
    console.print(
        "\n[bold]2. Notify bucket[/bold] — surface in digest, not realtime, never auto-marked."
    )
    notify_emails = _list_prompt(
        "Senders to notify", ["alerts@sentry.io", "no-reply@pagerduty.com"]
    )
    notify_subjects = _list_prompt(
        "Subject substrings to notify", ["incident", "deploy failed", "outage"]
    )

    # 3. Ignore
    console.print(
        "\n[bold]3. Ignore bucket[/bold] — auto-mark-read, silent. Be aggressive on obvious junk."
    )
    ignore_emails = _list_prompt("Senders to ignore", ["noreply@medium.com", "deals@retailer.example"])
    ignore_domains = _list_prompt("Domains to ignore", ["@offers.example.com"])
    ignore_subjects = _list_prompt(
        "Subject substrings to ignore", ["Your order shipped", "Receipt for"]
    )

    # 4. NL rules
    console.print(
        "\n[bold]4. Natural-language rules[/bold] — for patterns headers can't express. "
        "LLM sees these at classify time."
    )
    nl_rules = _list_prompt(
        "NL rules (one per line)",
        [
            "Mail from @yourcompany.com asking me a direct question → respond",
            "Order confirmation or shipping notification → ignore + auto-mark",
            "Vendor cold pitch unless mentions our product → ignore",
        ],
    )

    # 5. Risk tolerance
    console.print("\n[bold]5. Risk tolerance[/bold] — how aggressive should auto-mark be?")
    risk = Prompt.ask(
        "  Pick",
        choices=["conservative", "balanced", "aggressive"],
        default="conservative",
    )
    risk_map = {"conservative": 0.95, "balanced": 0.90, "aggressive": 0.85}
    auto_mark_floor = risk_map[risk]

    # Preview
    new_rules = _build_rules_block(
        vip_emails,
        vip_domains,
        notify_emails,
        notify_subjects,
        ignore_emails,
        ignore_domains,
        ignore_subjects,
    )

    console.print("\n[bold]Summary[/bold]")
    for r in new_rules:
        console.print(f"  + rule [cyan]{r['name']}[/cyan] → [yellow]{r['bucket']}[/yellow]")
    console.print(f"  + auto_mark_min_confidence = [magenta]{auto_mark_floor}[/magenta] ({risk})")
    console.print(f"  + {len(nl_rules)} NL rule(s)")

    if not (new_rules or nl_rules):
        console.print("[yellow]Nothing to write. Cancelled.[/yellow]")
        return

    if not Confirm.ask(f"Write to [bold]{rules_path}[/bold]?", default=True):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    merged = _merge_yaml(rules_path, new_rules, nl_rules, auto_mark_floor)
    backup = rules_path.with_suffix(rules_path.suffix + ".bak")
    if rules_path.exists():
        backup.write_text(rules_path.read_text())
        console.print(f"[dim]Backup → {backup}[/dim]")
    rules_path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True))
    console.print(f"[green]Wrote {rules_path}[/green]")
    console.print(
        "[dim]Next: run `mail-agent eval run` to see if your existing fixtures still pass.[/dim]"
    )

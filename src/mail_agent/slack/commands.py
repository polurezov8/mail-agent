"""Handlers for `/mail` slash command subcommands.

`/mail` (no args) or `/mail triage` → run a triage pass (async).
`/mail status`                       → counters + last-run timestamp.
`/mail rules`                        → list configured rules.
`/mail recent [N]`                   → last N mark-read audit entries (default 10).
`/mail help`                         → list subcommands.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from ..config import load_config
from ..graph.build import build_graph
from ..models import AutoMarkResult, SurfaceResult
from ..store.stats import get_recent_audit, get_status

Respond = Callable[..., Any]


HELP_TEXT = (
    "*`/mail` — Gmail triage agent*\n"
    "Tip: in DM, type any question about your inbox — e.g. _subscriptions from Apple last 3 months_.\n"
    "\n"
    "*🚀 Run*\n"
    "• `/mail` — triage now (async)\n"
    "• `/mail search <natural-language>` — find mail by NL query\n"
    "\n"
    "*📊 Inspect*\n"
    "• `/mail brief [hours]` — narrative summary of recent activity\n"
    "• `/mail stats [day|week|month|all]` — counts + charts\n"
    "• `/mail status` — counters, last run\n"
    "• `/mail recent [N]` — last N mark-read entries\n"
    "• `/mail review [N]` — review uncertain auto-marks\n"
    "\n"
    "*🔧 Tune*\n"
    "• `/mail rules` — list configured rules\n"
    '• `/mail rule add "<text>"` — add an NL rule the LLM applies\n'
    "• `/mail corrections list` — list user corrections\n"
    "• `/mail corrections undo <ID>` — soft-undo a sender-level override\n"
    "\n"
    "• `/mail help` — this message"
)


def _format_relative(iso_ts: str | None) -> str:
    if not iso_ts:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def handle_help(respond: Respond) -> None:
    respond(text=HELP_TEXT, response_type="ephemeral")


def handle_status(respond: Respond) -> None:
    s = get_status()
    bucket_line = " · ".join(f"{k}: *{v}*" for k, v in s.bucket_counts.items())
    text = (
        f"*mail-agent status*\n"
        f"• Last run: {_format_relative(s.last_processed_at)}\n"
        f"• Processed: *{s.total_processed}* total · *{s.processed_today}* today\n"
        f"• Marked read: *{s.marked_read_total}* total · *{s.marked_read_today}* today\n"
        f"• Buckets: {bucket_line}"
    )
    respond(text=text, response_type="ephemeral")


def handle_rules(respond: Respond) -> None:
    cfg = load_config("config/rules.yaml")
    lines = ["*Configured rules*"]
    for r in cfg.rules:
        auto = " · auto-mark" if r.auto_mark_read else ""
        lines.append(f"• `{r.name}` → `{r.bucket.value}`{auto} — _{r.description}_")
    lines.append(
        f"\n_LLM fallback:_ `{cfg.llm.model_fast}` → `{cfg.llm.model_smart}` "
        f"@ conf<{cfg.llm.confidence_threshold}, auto-mark floor {cfg.llm.auto_mark_min_confidence}"
    )
    respond(text="\n".join(lines), response_type="ephemeral")


def handle_recent(respond: Respond, limit: int) -> None:
    entries = get_recent_audit(limit=limit)
    if not entries:
        respond(text="_No mark-read entries yet._", response_type="ephemeral")
        return
    from ..visualize import format_rule_name

    lines = [f"*Recent {len(entries)} mark-read entries*"]
    for e in entries:
        tag = " (dry-run)" if e.dry_run else ""
        rule = format_rule_name(e.rule_name) if e.rule_name else e.source
        lines.append(
            f"• {_format_relative(e.marked_at)}{tag} — _{e.from_email}_ "
            f"· *{e.subject[:80]}* · _{rule}_ (conf {e.confidence:.2f})"
        )
    respond(text="\n".join(lines), response_type="ephemeral")


def _run_triage_background(respond: Respond) -> None:
    try:
        cfg = load_config("config/rules.yaml")
        graph = build_graph()
        # Run real Slack dispatch — slash triage is a user-initiated run that
        # should still post respond/notify cards to the DM. The ephemeral
        # summary returned at the end is additive, not a replacement.
        state = graph.invoke(
            {
                "config": cfg,
                "skip_processed": True,
            }
        )
        results = state.get("results", [])
        n = len(results)
        n_marked = sum(1 for r in results if isinstance(r, AutoMarkResult))
        n_surface = sum(1 for r in results if isinstance(r, SurfaceResult))
        respond(
            text=(
                f"✅ Triage done. *{n}* mails processed · "
                f"*{n_marked}* auto-marked · *{n_surface}* surfaced.\n"
                f"_Use `/mail recent` to see what was marked._"
            ),
            response_type="ephemeral",
        )
    except Exception as exc:
        respond(text=f":warning: Triage failed: `{exc}`", response_type="ephemeral")


def handle_triage(respond: Respond) -> None:
    respond(text="🔄 Triaging…", response_type="ephemeral")
    threading.Thread(target=_run_triage_background, args=(respond,), daemon=True).start()


def _pick_diverse(candidates: list[dict], n: int) -> list[dict]:
    """Pick up to n candidates with distinct from_email (newest per sender)."""
    picked: list[dict] = []
    seen: set[str] = set()
    for c in candidates:
        sender = (c.get("from_email") or "").lower()
        if sender in seen:
            continue
        seen.add(sender)
        picked.append(c)
        if len(picked) >= n:
            break
    return picked


def handle_rule_add(respond: Respond, rule_text: str) -> None:
    from pathlib import Path

    from ..rules.edit import add_nl_rule

    rule_text = rule_text.strip().strip('"').strip("'")
    if not rule_text:
        respond(text=":warning: Empty rule text.", response_type="ephemeral")
        return
    try:
        added, total = add_nl_rule(rule_text, Path("config/rules.yaml"))
    except Exception as exc:
        respond(text=f":warning: Failed to add rule: `{exc}`", response_type="ephemeral")
        return
    if added:
        respond(
            text=f"✅ Added NL rule (`{total}` total): _{rule_text}_",
            response_type="ephemeral",
        )
    else:
        respond(
            text=f":information_source: Rule already exists ({total} total).",
            response_type="ephemeral",
        )


def handle_corrections_list(respond: Respond, limit: int) -> None:
    from ..store.sqlite import list_corrections

    rows = list_corrections(limit=limit)
    if not rows:
        respond(text="_No corrections yet._", response_type="ephemeral")
        return
    lines = [f"*Corrections* (latest {len(rows)})"]
    for r in rows:
        scope = "sender-wide" if r["apply_to_sender"] else "one-off"
        lines.append(
            f"• `#{r['id']}` {_format_relative(r['corrected_at'])} — "
            f"_{r.get('from_email') or '-'}_ · "
            f"*{(r.get('subject') or '')[:60]}* · "
            f"`{r['original_bucket']} → {r['corrected_bucket']}` ({scope})"
        )
    lines.append("\n_Undo with `/mail corrections undo <ID>`._")
    respond(text="\n".join(lines), response_type="ephemeral")


def handle_corrections_undo(respond: Respond, correction_id: int) -> None:
    from ..store.sqlite import deactivate_correction

    if deactivate_correction(correction_id):
        respond(
            text=f"✅ Correction `#{correction_id}` deactivated. Sender override removed.",
            response_type="ephemeral",
        )
    else:
        respond(
            text=f":warning: No correction `#{correction_id}` found.",
            response_type="ephemeral",
        )


def _run_search_background(respond: Respond, nl_query: str) -> None:
    try:
        from ..config import load_config
        from ..gmail.accounts import load_accounts
        from ..gmail.client import search_messages
        from ..search import build_gmail_query
        from .blocks import search_results_blocks
        from .client import get_channel_id, get_client, is_configured

        cfg = load_config("config/rules.yaml")
        plan = build_gmail_query(nl_query, cfg.llm)
        if not plan.gmail_query:
            respond(
                text=":warning: Couldn't build a Gmail query from that.",
                response_type="ephemeral",
            )
            return

        accounts = [a for a in load_accounts() if a.is_authorized]
        if not accounts:
            respond(text=":warning: No authorized Gmail accounts.", response_type="ephemeral")
            return

        effective_limit = plan.suggested_limit or 10
        hits = []
        for acct in accounts:
            try:
                hits.extend(search_messages(acct, plan.gmail_query, limit=effective_limit))
            except Exception as exc:
                respond(
                    text=f":warning: Search failed on `{acct.name}`: `{exc}`",
                    response_type="ephemeral",
                )
                return
        hits.sort(key=lambda m: m.received_at, reverse=True)
        top = hits[:effective_limit]

        if not is_configured():
            respond(text=":warning: Slack not configured.", response_type="ephemeral")
            return
        client = get_client()
        channel = get_channel_id()
        client.chat_postMessage(
            channel=channel,
            blocks=search_results_blocks(nl_query, plan.gmail_query, top, plan.reasoning),
            text=f"Search · {nl_query[:80]}",
            unfurl_links=False,
            unfurl_media=False,
        )
        respond(
            text=f"🔍 Posted {len(top)} result(s) for: _{nl_query}_",
            response_type="ephemeral",
        )
    except Exception as exc:
        respond(text=f":warning: Search failed: `{exc}`", response_type="ephemeral")


def handle_search(respond: Respond, nl_query: str) -> None:
    respond(text=f"🔍 Searching for _{nl_query}_…", response_type="ephemeral")
    threading.Thread(
        target=_run_search_background, args=(respond, nl_query), daemon=True
    ).start()


def _run_ask_background(respond: Respond, question: str) -> None:
    try:
        from ..analyst import analyse_inbox
        from ..config import load_config
        from .blocks import ask_result_blocks
        from .client import get_channel_id, get_client, is_configured

        cfg = load_config("config/rules.yaml")
        answer = analyse_inbox(question, cfg.llm)

        if not is_configured():
            respond(text=":warning: Slack not configured.", response_type="ephemeral")
            return
        client = get_client()
        channel = get_channel_id()
        client.chat_postMessage(
            channel=channel,
            blocks=ask_result_blocks(question, answer),
            text=f"Inbox answer · {question[:80]}",
            unfurl_links=False,
            unfurl_media=False,
        )
        respond(text="✅ Done.", response_type="ephemeral")
    except Exception as exc:
        respond(text=f":warning: Analysis failed: `{exc}`", response_type="ephemeral")


def handle_ask(respond: Respond, question: str) -> None:
    respond(text=f"🔎 Analysing your inbox for: _{question}_…", response_type="ephemeral")
    threading.Thread(
        target=_run_ask_background, args=(respond, question), daemon=True
    ).start()


def handle_stats(respond: Respond, period: str) -> None:
    from ..store.stats import compute_metrics
    from .blocks import stats_blocks
    from .client import get_channel_id, get_client, is_configured

    snapshot = compute_metrics(period=period)
    if not is_configured():
        respond(text=":warning: Slack not configured.", response_type="ephemeral")
        return
    client = get_client()
    channel = get_channel_id()
    client.chat_postMessage(
        channel=channel,
        blocks=stats_blocks(snapshot),
        text=f"Mail Stats · {period}",
        unfurl_links=False,
        unfurl_media=False,
    )
    respond(
        text=f"📈 Stats posted ({period}, {snapshot.total_processed} processed).",
        response_type="ephemeral",
    )


def handle_brief(respond: Respond, hours: int) -> None:
    from ..brief import build_brief
    from .blocks import brief_blocks
    from .client import get_channel_id, get_client, is_configured

    summary = build_brief(hours=hours)
    if not is_configured():
        respond(text=":warning: Slack not configured.", response_type="ephemeral")
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
    respond(
        text=(f"📊 Brief posted (last {hours}h, {summary.processed_total} processed)."),
        response_type="ephemeral",
    )


def handle_review(respond: Respond, limit: int) -> None:
    from .blocks import review_blocks
    from .client import get_channel_id, get_client, is_configured
    from ..store.sqlite import get_review_candidates, mark_reviewed

    if not is_configured():
        respond(text=":warning: Slack not configured.", response_type="ephemeral")
        return

    pool = get_review_candidates(pool_size=50)
    if not pool:
        respond(
            text="_No uncertain auto-marks to review (LLM-classified, conf 0.85–0.95)._ "
            "Nothing has happened yet, or you've already reviewed everything.",
            response_type="ephemeral",
        )
        return

    chosen = _pick_diverse(pool, limit)
    if not chosen:
        respond(text="_No candidates after diversity filter._", response_type="ephemeral")
        return

    client = get_client()
    channel = get_channel_id()
    client.chat_postMessage(
        channel=channel,
        blocks=review_blocks(chosen),
        text=f"Review batch · {len(chosen)} items",
        unfurl_links=False,
        unfurl_media=False,
    )
    mark_reviewed([c["id"] for c in chosen])
    respond(
        text=f"✅ Posted review batch of {len(chosen)} mails. Tap *Wrong bucket* on any you'd reclassify.",
        response_type="ephemeral",
    )


def dispatch(text: str, respond: Respond, fallback_to_search: bool = False) -> None:
    """Parse `text` and route to a subcommand.

    Used both by `/mail` (slash) and by DM messages (free-text in a bot DM).
    For DMs, `fallback_to_search=True` makes any unknown text route to
    handle_ask — so "subscriptions from Apple last 3 months" works without
    needing a slash command prefix.
    """
    parts = text.strip().split()
    if not parts:
        handle_triage(respond)
        return
    sub = parts[0].lower()
    if sub in ("triage", "run"):
        handle_triage(respond)
    elif sub == "status":
        handle_status(respond)
    elif sub == "rules":
        handle_rules(respond)
    elif sub == "recent":
        limit = 10
        if len(parts) > 1:
            try:
                limit = max(1, min(int(parts[1]), 50))
            except ValueError:
                pass
        handle_recent(respond, limit)
    elif sub == "review":
        limit = 5
        if len(parts) > 1:
            try:
                limit = max(1, min(int(parts[1]), 10))
            except ValueError:
                pass
        handle_review(respond, limit)
    elif sub == "brief":
        hours = 24
        if len(parts) > 1:
            try:
                hours = max(1, min(int(parts[1]), 168))
            except ValueError:
                pass
        handle_brief(respond, hours)
    elif sub == "stats":
        period = parts[1].lower() if len(parts) > 1 else "week"
        if period not in ("day", "week", "month", "all"):
            period = "week"
        handle_stats(respond, period)
        return
    elif sub == "search":
        if len(parts) < 2:
            respond(
                text="_Usage:_ `/mail search anything from Jane last week`",
                response_type="ephemeral",
            )
            return
        nl_query = " ".join(parts[1:]).strip().strip('"').strip("'")
        handle_search(respond, nl_query)
        return
    elif sub == "rule":
        action = parts[1].lower() if len(parts) > 1 else ""
        if action == "add" and len(parts) > 2:
            rule_text = " ".join(parts[2:])
            handle_rule_add(respond, rule_text)
        else:
            respond(
                text='_Usage:_ `/mail rule add "anything from acme.com → respond"`',
                response_type="ephemeral",
            )
        return
    elif sub == "corrections":
        action = parts[1].lower() if len(parts) > 1 else "list"
        if action == "list":
            limit = 20
            if len(parts) > 2:
                try:
                    limit = max(1, min(int(parts[2]), 50))
                except ValueError:
                    pass
            handle_corrections_list(respond, limit)
        elif action == "undo" and len(parts) > 2:
            try:
                cid = int(parts[2])
            except ValueError:
                respond(
                    text=":warning: Usage: `/mail corrections undo <ID>`",
                    response_type="ephemeral",
                )
                return
            handle_corrections_undo(respond, cid)
        else:
            respond(
                text="_Usage:_ `/mail corrections list [N]` · `/mail corrections undo <ID>`",
                response_type="ephemeral",
            )
        return
    elif sub in ("help", "?", "-h", "--help"):
        handle_help(respond)
    else:
        if fallback_to_search:
            handle_ask(respond, text.strip())
        else:
            respond(
                text=f":grey_question: Unknown subcommand `{sub}`. Try `/mail help`.",
                response_type="ephemeral",
            )

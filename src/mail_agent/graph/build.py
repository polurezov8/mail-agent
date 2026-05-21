"""LangGraph wiring.

Linear-with-branches: fetch → triage → persist → [mark_read?] → [slack_dispatch?] → report.
Conditional edges skip nodes that would no-op, so the graph reflects actual flow
instead of hiding decisions inside node bodies.
"""

from __future__ import annotations

from collections import defaultdict

from langgraph.graph import END, START, StateGraph

from ..llm.classifier import classify
from ..models import (
    AccountName,
    AutoMarkResult,
    Bucket,
    EmailMessage,
    MessageId,
    SurfaceResult,
    TriageDecision,
    TriageResult,
)
from ..rules.header import match_first
from .state import GraphState


def _gate(
    email: EmailMessage,
    decision: TriageDecision,
    min_confidence: float,
) -> TriageResult:
    """Single point where the auto-mark gate is enforced. If all conditions
    pass → AutoMarkResult (can be marked). Otherwise → SurfaceResult.

    Three gates: bucket=ignore, classifier opted in (decision.auto_mark),
    and confidence ≥ floor. The opt-in comes from the rule's auto_mark_read
    flag (header path), the LLM's auto_mark output (LLM path), or the
    user's explicit override (user_correction path).
    """
    if (
        decision.bucket == Bucket.IGNORE
        and decision.auto_mark
        and decision.confidence >= min_confidence
    ):
        return AutoMarkResult(email=email, decision=decision)
    return SurfaceResult(email=email, decision=decision)


def node_fetch(state: GraphState) -> GraphState:
    if state.get("mock"):
        from ..mock import MOCK_INBOX

        return {"inbox": MOCK_INBOX}

    from ..gmail.accounts import load_accounts
    from ..gmail.client import fetch_unread
    from ..store.sqlite import init_db, processed_ids

    init_db()
    accounts = load_accounts()
    account_filter = state.get("accounts")
    if account_filter:
        accounts = [a for a in accounts if a.name in account_filter]
    if not accounts:
        return {"inbox": []}

    query = state.get("query")
    limit = state.get("limit")
    skip_processed = state.get("skip_processed", True)

    inbox: list[EmailMessage] = []
    for account in accounts:
        if not account.is_authorized:
            print(
                f"[mail-agent] account '{account.name}' not authorized — "
                f"run `mail-agent setup-gmail`. Skipping."
            )
            continue
        fetched = fetch_unread(account, limit=limit, query=query)
        if skip_processed:
            ids = [m.id for m in fetched]
            already = processed_ids(AccountName(account.name), ids)
            fetched = [m for m in fetched if m.id not in already]
        inbox.extend(fetched)
    return {"inbox": inbox}


def node_triage(state: GraphState) -> GraphState:
    cfg = state["config"]
    min_conf = cfg.llm.auto_mark_min_confidence
    mock = bool(state.get("mock", False))

    from ..store.sqlite import sender_override

    results: list[TriageResult] = []
    for email in state["inbox"]:
        # Stage 0: user corrections override everything.
        override = sender_override(email.from_email)
        if override:
            override_bucket = Bucket(override)
            decision = TriageDecision(
                bucket=override_bucket,
                rule_name=None,
                reasoning=f"Sender-level user correction → {override}.",
                confidence=1.0,
                source="user_correction",
                auto_mark=override_bucket == Bucket.IGNORE,
            )
        else:
            decision = match_first(email, cfg.rules)
            if decision is None:
                if cfg.llm.enabled:
                    thread_context = _fetch_thread_context(email, mock=mock)
                    decision = classify(email, cfg.rules, cfg.llm, thread_context)
                else:
                    decision = TriageDecision(
                        bucket=Bucket.NOTIFY,
                        rule_name=None,
                        reasoning="No rule matched; LLM disabled. Defaulting to notify.",
                        confidence=0.0,
                        source="header_rule",
                    )
        results.append(_gate(email, decision, min_conf))
    return {"results": results}


def _fetch_thread_context(email: EmailMessage, mock: bool = False):
    """Pull up to 3 prior messages in this thread. Skipped in mock mode
    (no Gmail connectivity) and on any API failure (graceful degradation).
    Returns None when nothing useful is available."""
    if mock:
        return None
    try:
        from ..gmail.accounts import load_accounts
        from ..gmail.client import fetch_thread_history

        accounts = {a.name: a for a in load_accounts()}
        account = accounts.get(email.account)
        if account is None or not account.is_authorized:
            return None
        prior = fetch_thread_history(account, email.thread_id, exclude_message_id=email.id, limit=3)
        return prior or None
    except Exception:
        return None


def node_persist(state: GraphState) -> GraphState:
    # Skip persistence entirely for mock OR dry-run. A dry-run that wrote
    # to processed_messages would silently suppress those mails on every
    # future normal run — the opposite of "preview".
    if state.get("mock") or state.get("dry_run"):
        return {}
    from ..store.sqlite import mark_processed

    mark_processed(state["results"])
    return {}


def node_mark_read(state: GraphState) -> GraphState:
    """Execute mark-as-read. Only AutoMarkResult variants ever reach Gmail."""
    from ..actions.mark_read import mark_read
    from ..gmail.accounts import load_accounts
    from ..store.sqlite import log_mark_read

    results = state.get("results", [])
    to_mark: list[AutoMarkResult] = [r for r in results if isinstance(r, AutoMarkResult)]
    # Routing guarantees this won't be called with no targets, but keep guard
    # for direct invocation in tests.
    if not to_mark:
        return {}

    dry_run = bool(state.get("dry_run", False))
    log_mark_read(to_mark, dry_run=dry_run)
    if dry_run:
        return {}

    by_account: dict[AccountName, list[MessageId]] = defaultdict(list)
    for r in to_mark:
        by_account[r.email.account].append(r.email.id)

    accounts = {AccountName(a.name): a for a in load_accounts()}
    for name, ids in by_account.items():
        acct = accounts.get(name)
        if acct is None:
            continue
        mark_read(acct, ids)
    return {}


def node_slack_dispatch(state: GraphState) -> GraphState:
    """Send results to Slack: respond → realtime, notify → digest,
    uncertain ignore → review-style digest, confident auto-marked ignore → silent."""
    # Skip dispatch when caller explicitly opted out OR running mock.
    # (Routing usually short-circuits before us; this is the belt-and-braces.)
    if state.get("mock") or state.get("no_slack"):
        return {}
    from ..slack.client import is_configured
    from ..slack.dispatcher import dispatch

    if not is_configured():
        return {}

    summary = dispatch(state.get("results", []))
    return {"slack_summary": summary}


def node_report(state: GraphState) -> GraphState:
    from rich.console import Console
    from rich.table import Table

    results = state.get("results", [])
    if not results:
        Console().print("[dim]No new unread mail.[/dim]")
        return {}

    dry_run = bool(state.get("dry_run", False))
    mock = bool(state.get("mock", False))
    marked_suffix = " (dry-run)" if dry_run else " (mock)" if mock else ""

    table = Table(title=f"Triage results ({len(results)})")
    table.add_column("Account", style="magenta")
    table.add_column("From", style="cyan", overflow="fold")
    table.add_column("Subject", overflow="fold")
    table.add_column("Bucket")
    table.add_column("Rule / source")
    table.add_column("Conf", justify="right")
    table.add_column("Action", justify="center")
    for r in results:
        action_label = f"mark-read{marked_suffix}" if isinstance(r, AutoMarkResult) else ""
        table.add_row(
            r.email.account or "-",
            r.email.from_email,
            r.email.subject,
            r.decision.bucket.value,
            r.decision.rule_name or r.decision.source,
            f"{r.decision.confidence:.2f}",
            action_label,
        )
    Console().print(table)
    n_marked = sum(1 for r in results if isinstance(r, AutoMarkResult))
    if n_marked:
        if mock:
            verb = "would mark (mock — no write)"
        elif dry_run:
            verb = "would mark (dry-run)"
        else:
            verb = "marked"
        Console().print(f"[green]{verb} {n_marked} as read.[/green]")
    summary = state.get("slack_summary")
    if summary:
        Console().print(
            f"[cyan]Slack: {summary['realtime_posts']} realtime, "
            f"{summary['digest_mails']} in digest.[/cyan]"
        )
    return {}


# ── routing predicates (used by conditional edges) ────────────────────────────


def _route_after_fetch(state: GraphState) -> str:
    """Skip the entire triage chain when there's nothing to do."""
    return "triage" if state.get("inbox") else "report"


def _route_to_slack_or_report(state: GraphState) -> str:
    """Skip Slack when mock/no_slack flag set OR there's nothing surface-worthy."""
    if state.get("mock") or state.get("no_slack"):
        return "report"
    results = state.get("results", [])
    if not any(isinstance(r, SurfaceResult) for r in results):
        return "report"
    return "slack_dispatch"


def _route_after_persist(state: GraphState) -> str:
    """Skip mark_read when nothing was gated to auto-mark.

    Also honors mock/no_slack: if there's no auto-mark target AND Slack is
    suppressed, jump straight to report instead of slack_dispatch (which the
    other route would correctly skip but we avoid the extra hop)."""
    results = state.get("results", [])
    if any(isinstance(r, AutoMarkResult) for r in results):
        return "mark_read"
    # No auto-mark targets. Decide between slack_dispatch and report.
    if state.get("mock") or state.get("no_slack"):
        return "report"
    if not any(isinstance(r, SurfaceResult) for r in results):
        return "report"
    return "slack_dispatch"


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("fetch", node_fetch)
    g.add_node("triage", node_triage)
    g.add_node("persist", node_persist)
    g.add_node("mark_read", node_mark_read)
    g.add_node("slack_dispatch", node_slack_dispatch)
    g.add_node("report", node_report)

    g.add_edge(START, "fetch")
    g.add_conditional_edges("fetch", _route_after_fetch, {"triage": "triage", "report": "report"})
    g.add_edge("triage", "persist")
    g.add_conditional_edges(
        "persist",
        _route_after_persist,
        {"mark_read": "mark_read", "slack_dispatch": "slack_dispatch"},
    )
    g.add_conditional_edges(
        "mark_read",
        _route_to_slack_or_report,
        {"slack_dispatch": "slack_dispatch", "report": "report"},
    )
    g.add_edge("slack_dispatch", "report")
    g.add_edge("report", END)
    return g.compile()

"""Application use cases — one named function per agent action.

This is the seam between frontends (CLI, Slack listener) and the domain.
Every use case takes inputs, bundles config + account resolution + the
domain call, and returns a typed result. Formatting and delivery belong
to the caller, not here.

If you want to add a third frontend (HTTP, queue worker, replay tool), wire it
to these functions; do not duplicate the orchestration in the new frontend.
"""

from __future__ import annotations

from dataclasses import dataclass

from .analyst import AnalysisAnswer, analyse_inbox
from .brief import BriefSummary, build_brief
from .config import Config, load_config
from .models import AutoMarkResult, EmailMessage, SurfaceResult, TriageResult
from .search import SearchPlan, build_gmail_query
from .slack.types import DispatchSummary
from .store.stats import MetricsSnapshot, compute_metrics


DEFAULT_CONFIG_PATH = "config/rules.yaml"


@dataclass(frozen=True)
class TriageRunResult:
    results: list[TriageResult]
    slack_summary: DispatchSummary | None

    @property
    def n_total(self) -> int:
        return len(self.results)

    @property
    def n_auto_marked(self) -> int:
        return sum(1 for r in self.results if isinstance(r, AutoMarkResult))

    @property
    def n_surfaced(self) -> int:
        return sum(1 for r in self.results if isinstance(r, SurfaceResult))


@dataclass(frozen=True)
class SearchResult:
    query: str
    plan: SearchPlan
    hits: list[EmailMessage]


# ---------------------------------------------------------------- #
# Use cases
# ---------------------------------------------------------------- #


def triage(
    cfg: Config,
    *,
    mock: bool = False,
    query: str | None = None,
    limit: int | None = None,
    skip_processed: bool = True,
    dry_run: bool = False,
    no_slack: bool = False,
    accounts: list[str] | None = None,
) -> TriageRunResult:
    """Run the full triage pipeline. Returns the gated results + Slack summary
    (if Slack was invoked). Persistence and Gmail writes happen inside the graph."""
    from .graph.build import build_graph

    state = build_graph().invoke({
        "config": cfg,
        "mock": mock,
        "query": query,
        "limit": limit,
        "skip_processed": skip_processed,
        "dry_run": dry_run,
        "no_slack": no_slack,
        "accounts": accounts,
    })
    return TriageRunResult(
        results=list(state.get("results", [])),
        slack_summary=state.get("slack_summary"),
    )


def search(
    nl_query: str,
    cfg: Config,
    *,
    limit: int | None = None,
    accounts: list[str] | None = None,
) -> SearchResult:
    """Translate NL query → Gmail query, fetch from every authorized account
    in scope, then return the plan + sorted hits (top N).

    `limit` defaults to plan.suggested_limit when None.
    """
    from .gmail.accounts import load_accounts
    from .gmail.client import search_messages

    plan = build_gmail_query(nl_query, cfg.llm)
    effective_limit = plan.suggested_limit or limit or 10

    in_scope = [
        a for a in load_accounts()
        if a.is_authorized and (accounts is None or a.name in accounts)
    ]

    hits: list[EmailMessage] = []
    for acct in in_scope:
        hits.extend(search_messages(acct, plan.gmail_query, limit=effective_limit))
    hits.sort(key=lambda m: m.received_at, reverse=True)
    hits = hits[:effective_limit]

    return SearchResult(query=nl_query, plan=plan, hits=hits)


def stats(
    period: str = "week",
    *,
    accounts: list[str] | None = None,
) -> MetricsSnapshot:
    """Snapshot of triage metrics for the period — counts, top rules, daily."""
    return compute_metrics(period=period, accounts=accounts)


def brief(
    hours: int = 24,
    *,
    accounts: list[str] | None = None,
) -> BriefSummary:
    """Narrative activity Brief for the last N hours."""
    return build_brief(hours=hours, accounts=accounts)


def ask(question: str, cfg: Config) -> AnalysisAnswer:
    """Free-form inbox analyst question. Read-only, multi-stage LLM pipeline."""
    return analyse_inbox(question, cfg.llm)


# ---------------------------------------------------------------- #
# Convenience
# ---------------------------------------------------------------- #


def load_default_config() -> Config:
    """One-liner for callers that don't need a custom config path."""
    return load_config(DEFAULT_CONFIG_PATH)

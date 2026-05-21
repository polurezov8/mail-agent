from __future__ import annotations

from typing import TypedDict

from ..config import Config
from ..models import EmailMessage, TriageResult


class GraphState(TypedDict, total=False):
    config: Config
    inbox: list[EmailMessage]
    results: list[TriageResult]
    mock: bool
    query: str | None
    limit: int | None
    skip_processed: bool
    dry_run: bool
    no_slack: bool
    slack_summary: dict
    accounts: list[str] | None  # if set, only process accounts with these names

"""List-Unsubscribe parsing + execution.

RFC 2369 (List-Unsubscribe) + RFC 8058 (List-Unsubscribe-Post one-click).

Two unsubscribe types found in the wild:
  • One-click HTTPS POST (preferred): sender opts in via List-Unsubscribe-Post header.
  • mailto: send empty email to the listed address.

Strategy: prefer one-click POST. Fall back to nothing (we don't auto-send
mailto here — too easy to leak the user's address to spammers).
"""

from __future__ import annotations

import re
from typing import Literal

import httpx
from pydantic import BaseModel


class UnsubscribeOption(BaseModel):
    """What we can do for a single sender."""

    method: Literal["one_click_post", "mailto", "none"]
    url: str | None = None  # one_click_post target
    mailto: str | None = None  # mailto target


def parse_list_unsubscribe(headers: dict[str, str]) -> UnsubscribeOption:
    """Parse List-Unsubscribe + List-Unsubscribe-Post headers.

    Returns UnsubscribeOption with method='none' when nothing actionable.
    """
    raw = headers.get("List-Unsubscribe", "")
    post_header = headers.get("List-Unsubscribe-Post", "")

    # Extract URLs and mailtos from <...> tokens, comma-separated.
    urls: list[str] = []
    mailtos: list[str] = []
    for token in re.findall(r"<([^>]+)>", raw):
        token = token.strip()
        if token.lower().startswith("mailto:"):
            mailtos.append(token[len("mailto:") :])
        elif token.lower().startswith(("http://", "https://")):
            urls.append(token)

    one_click = "one-click" in post_header.lower()
    if one_click and urls:
        # Prefer HTTPS over HTTP.
        url = next((u for u in urls if u.startswith("https://")), urls[0])
        return UnsubscribeOption(method="one_click_post", url=url)
    if mailtos:
        return UnsubscribeOption(method="mailto", mailto=mailtos[0])
    return UnsubscribeOption(method="none")


class UnsubscribeResult(BaseModel):
    sender: str
    method: str
    success: bool
    detail: str = ""


def perform_unsubscribe(
    sender: str, option: UnsubscribeOption, *, timeout: float = 10.0
) -> UnsubscribeResult:
    """Execute the unsubscribe. Only one_click_post is auto-executed —
    mailto requires sending mail and we don't have send scope."""
    if option.method == "one_click_post" and option.url:
        try:
            resp = httpx.post(
                option.url,
                data={"List-Unsubscribe": "One-Click"},
                timeout=timeout,
                follow_redirects=True,
            )
            ok = 200 <= resp.status_code < 400
            return UnsubscribeResult(
                sender=sender,
                method="one_click_post",
                success=ok,
                detail=f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return UnsubscribeResult(
                sender=sender, method="one_click_post", success=False, detail=str(exc)
            )
    if option.method == "mailto":
        return UnsubscribeResult(
            sender=sender,
            method="mailto",
            success=False,
            detail=f"mailto requires manual action: {option.mailto}",
        )
    return UnsubscribeResult(
        sender=sender, method="none", success=False, detail="no List-Unsubscribe header"
    )

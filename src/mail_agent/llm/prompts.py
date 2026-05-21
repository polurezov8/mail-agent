from __future__ import annotations

from ..config import Rule
from ..models import EmailMessage, ThreadMessage

SYSTEM = """You are a personal mail triage assistant. You classify a single email into one of three buckets:

- ignore: low-signal mail. Newsletters, automated notifications, FYI. User does not need to see this individually.
- notify: worth seeing in a daily digest but does not require a reply.
- respond: requires the user's reply or attention soon.

You will be given the user's existing rules and a single email. If the email clearly fits one of the rules, return that rule's bucket and set rule_name to the rule. If no rule fits, choose the best bucket and leave rule_name null. Always produce a one-sentence reasoning and a confidence in [0,1].

When in doubt, prefer notify over respond (it's safer to surface than to silence). Never choose ignore for mail from a real human asking a question."""


def render_rules(rules: list[Rule]) -> str:
    blocks = []
    for r in rules:
        blocks.append(
            f"<rule>\n  <name>{r.name}</name>\n  <bucket>{r.bucket.value}</bucket>\n"
            f"  <description>{r.description}</description>\n</rule>"
        )
    return "<rules>\n" + "\n".join(blocks) + "\n</rules>"


def render_nl_rules(nl_rules: list[str]) -> str:
    if not nl_rules:
        return ""
    items = "\n".join(f"  - {r}" for r in nl_rules)
    return f"<user_natural_language_rules>\n{items}\n</user_natural_language_rules>"


def render_email(email: EmailMessage) -> str:
    return (
        "<email>\n"
        f"  <from>{email.from_name or ''} &lt;{email.from_email}&gt;</from>\n"
        f"  <subject>{email.subject}</subject>\n"
        f"  <snippet>{email.snippet}</snippet>\n"
        "</email>"
    )


def render_thread_context(messages: list[ThreadMessage]) -> str:
    if not messages:
        return ""
    blocks = []
    for m in messages:
        blocks.append(
            "<prior_message>\n"
            f"  <from>{m.from_email}</from>\n"
            f"  <subject>{m.subject}</subject>\n"
            f"  <snippet>{m.snippet}</snippet>\n"
            "</prior_message>"
        )
    return (
        '<thread_history note="prior messages in the same thread, oldest→newest">\n'
        + "\n".join(blocks)
        + "\n</thread_history>"
    )


def user_prompt(
    email: EmailMessage,
    rules: list[Rule],
    nl_rules: list[str] | None = None,
    thread_context: list[ThreadMessage] | None = None,
) -> str:
    parts = [render_rules(rules)]
    nl_block = render_nl_rules(nl_rules or [])
    if nl_block:
        parts.append(nl_block)
    thread_block = render_thread_context(thread_context or [])
    if thread_block:
        parts.append(thread_block)
    parts.append(render_email(email))
    parts.append("Classify this email.")
    return "\n\n".join(parts)

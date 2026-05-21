"""Block Kit builders. Output: list of Slack blocks dicts."""

from __future__ import annotations

import re

from ..models import SurfaceResult, TriageDecision


def _md_to_mrkdwn(text: str) -> str:
    """Convert GitHub Markdown to Slack mrkdwn.

    Handles: ### headings, **bold**, --- dividers, | tables |, [text](url).
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Table block: wrap consecutive | lines in a code block
        if line.strip().startswith("|"):
            table: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table.append(lines[i])
                i += 1
            result.append("```")
            result.extend(table)
            result.append("```")
            continue

        # --- divider → blank line
        if re.match(r"^\s*-{3,}\s*$", line):
            i += 1
            continue

        # ### Heading → *Heading*
        m = re.match(r"^#{1,6}\s+(.+)$", line)
        if m:
            result.append(f"*{m.group(1).strip()}*")
            i += 1
            continue

        # Normalize ** → * so **text** and **text* (mismatched) both become *text*
        line = line.replace("**", "*")

        # [text](url) → <url|text>
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", line)

        # Strip orphan trailing * (LLM sometimes wraps whole answer in *…*)
        # Only strip when star count is odd (unbalanced), meaning it's a dangling closer
        if line.count("*") % 2 == 1 and line.rstrip().endswith("*"):
            line = line.rstrip()[:-1].rstrip()

        result.append(line)
        i += 1

    return "\n".join(result)

Block = dict


def _gmail_url(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _mark_read_value(account: str, message_id: str) -> str:
    return f"{account}:{message_id}"


def _correct_value(account: str, message_id: str, original_bucket: str) -> str:
    return f"{account}:{message_id}:{original_bucket}"


def _account_badge(account: str) -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"_📬 {account}_"}],
    }


def _friendly_source(decision: TriageDecision) -> str:
    mapping = {
        "header_rule": "Rule",
        "llm_fast": "LLM",
        "llm_smart": "LLM",
        "user_correction": "User",
    }
    return mapping.get(decision.source, decision.source)


def _decision_context(decision: TriageDecision, prefix: str = "Why") -> dict:
    from ..visualize import format_rule_name

    rule_part = (
        f" · Rule: {format_rule_name(decision.rule_name)}" if decision.rule_name else ""
    )
    src = _friendly_source(decision)
    lines = [
        f"*{prefix}:* _{decision.reasoning}_",
        f"{src}{rule_part} · conf `{decision.confidence:.2f}`",
    ]
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(lines)}]}


def _row_actions(email, original_bucket: str, *, is_respond: bool = False) -> dict:
    """Primary button + Open + overflow(Wrong bucket). 3 elements, compact layout."""
    if is_respond:
        primary = {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in Gmail"},
            "style": "primary",
            "url": _gmail_url(email.thread_id),
            "action_id": "open_gmail",
        }
        secondary = {
            "type": "button",
            "text": {"type": "plain_text", "text": "Mark read"},
            "value": _mark_read_value(email.account, email.id),
            "action_id": "mark_read",
        }
    else:
        primary = {
            "type": "button",
            "text": {"type": "plain_text", "text": "Mark read"},
            "style": "danger",
            "value": _mark_read_value(email.account, email.id),
            "action_id": "mark_read",
        }
        secondary = {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in Gmail"},
            "url": _gmail_url(email.thread_id),
            "action_id": "open_gmail",
        }
    return {
        "type": "actions",
        "elements": [
            primary,
            secondary,
            {
                "type": "overflow",
                "action_id": "row_overflow",
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Wrong bucket"},
                        "value": _correct_value(email.account, email.id, original_bucket),
                    },
                ],
            },
        ],
    }


def respond_blocks(result: SurfaceResult, *, show_account: bool = False) -> list[Block]:
    """Single high-priority mail. One message per respond-bucket result."""
    from .format import clean_snippet, clean_subject

    email = result.email
    decision = result.decision
    sender = f"{email.from_name} <{email.from_email}>" if email.from_name else email.from_email
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔔 Needs response"},
        },
    ]
    if show_account:
        blocks.append(_account_badge(email.account))
    blocks += [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{clean_subject(email.subject)}*\n_From: {sender}_\n\n"
                    f">{_truncate(clean_snippet(email.snippet), 350)}"
                ),
            },
        },
        _decision_context(decision, prefix="Why respond"),
        _row_actions(email, decision.bucket.value, is_respond=True),
    ]
    return blocks


def stats_blocks(snapshot) -> list[Block]:
    """Stats card. Every table uses one fenced code block for consistent
    monospace rendering — no inline `code spans` (which Slack chunks visually
    and breaks alignment)."""
    from ..visualize import (
        BUCKET_GLYPH,
        format_rule_name,
        format_source,
        hbar,
        percent,
        sparkline,
    )

    s = snapshot
    period_label = {"day": "24 hours", "week": "7 days", "month": "30 days", "all": "all-time"}.get(
        s.period, s.period
    )
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📈 Stats · {period_label}"},
        },
    ]

    # Hero number — bold + label. Slack mrkdwn doesn't support true big text.
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{s.total_processed}* mails processed in the last {period_label}",
            },
        }
    )

    # Bucket breakdown — single code block. Color glyph in row label.
    if s.total_processed > 0:
        lines = []
        for b in ("respond", "notify", "ignore"):
            c = s.bucket_counts.get(b, 0)
            lines.append(
                f"{BUCKET_GLYPH[b]} {b:<8} "
                f"{hbar(c, s.total_processed, 22):<22} {c:>5}  {percent(c, s.total_processed)}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*By bucket*\n```\n" + "\n".join(lines) + "\n```",
                },
            }
        )

    # Source breakdown — human-readable labels.
    if s.source_counts:
        src_total = sum(s.source_counts.values())
        src_lines = []
        order = ["header_rule", "llm_fast", "llm_smart", "user_correction"]
        for src in order:
            c = s.source_counts.get(src, 0)
            if c == 0:
                continue
            label = format_source(src)
            src_lines.append(
                f"{label:<18} {hbar(c, src_total, 18):<18} {c:>5}  {percent(c, src_total)}"
            )
        if src_lines:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*By classifier source*\n```\n" + "\n".join(src_lines) + "\n```",
                    },
                }
            )

    # Top rules — human names, single code block.
    if s.rule_top:
        rule_max = s.rule_top[0].count
        rule_lines = [
            f"{format_rule_name(r.name)[:26]:<26} {hbar(r.count, rule_max, 14):<14} {r.count:>5}"
            for r in s.rule_top[:8]
        ]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Top rules*\n```\n" + "\n".join(rule_lines) + "\n```",
                },
            }
        )

    # Top senders — single code block. Emails not linkified inside ```.
    if s.top_senders_auto_marked:
        sender_max = s.top_senders_auto_marked[0].count
        sender_lines = [
            f"{t.from_email[:30]:<30} {hbar(t.count, sender_max, 12):<12} {t.count:>4}"
            for t in s.top_senders_auto_marked
        ]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Top auto-marked senders*\n```\n"
                    + "\n".join(sender_lines)
                    + "\n```",
                },
            }
        )

    # Daily sparkline — single code block.
    if s.daily and len(s.daily) > 1:
        values = [d.total for d in s.daily]
        spark = sparkline(values)
        first = s.daily[0].date
        last = s.daily[-1].date
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Daily activity*\n```\n{spark}\n"
                        f"{first}  →  {last}    peak: {max(values)}\n```"
                    ),
                },
            }
        )

    # Footer
    footer_parts: list[str] = []
    if s.marked_read:
        footer_parts.append(f"`{s.marked_read}` marked read")
    if s.corrections:
        footer_parts.append(f"`{s.corrections}` corrections")
    if s.uncertain_band:
        footer_parts.append(f"`{s.uncertain_band}` uncertain — `/mail review`")
    if footer_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(footer_parts)}],
            }
        )
    return blocks


def search_results_blocks(
    nl_query: str,
    gmail_query: str,
    results: list,
    reasoning: str = "",
    *,
    show_account: bool = False,
) -> list[Block]:
    """Search hits as a single Slack post."""
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔍 Search · {nl_query[:60]}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Translated to `{gmail_query}`"
                        + (f"\n_{reasoning}_" if reasoning else "")
                    ),
                }
            ],
        },
    ]
    if not results:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_No matches._"},
            }
        )
        return blocks
    for r in results:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{_truncate(r.subject, 120)}*\n"
                        f"_{r.from_email}_\n>{_truncate(r.snippet, 220)}"
                    ),
                },
            }
        )
        ctx_parts = [f"`{r.received_at.strftime('%Y-%m-%d %H:%M')}`"]
        if show_account:
            ctx_parts.append(f"_📬 {r.account}_")
        ctx_parts.append(f"<{_gmail_url(r.thread_id)}|Open in Gmail>")
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(ctx_parts)}],
            }
        )
    return blocks


def _bucket_bar_lines(bucket_counts: dict[str, int]) -> list[str]:
    """Color-glyph + bar + count + %. Designed for a single fenced code block
    so the bar/numbers stay perfectly aligned. (Slack's color emoji ARE allowed
    inside ``` blocks — they render fine, just inline like any glyph.)"""
    from ..visualize import BUCKET_GLYPH, hbar, percent

    total = sum(bucket_counts.values())
    if total == 0:
        return []
    lines = []
    for b in ("respond", "notify", "ignore"):
        c = bucket_counts.get(b, 0)
        lines.append(
            f"{BUCKET_GLYPH[b]} {b:<8} {hbar(c, total, 18):<18} {c:>4}  {percent(c, total)}"
        )
    return lines


def brief_blocks(summary) -> list[Block]:
    """Render BriefSummary as a Slack message: one section per dimension."""
    header_text = f"📊 Brief · last {summary.hours}h"
    blocks: list[Block] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text}},
    ]

    # Top-line counts
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Processed:* {summary.processed_total}   *Marked read:* {summary.marked_read}"
                ),
            },
        }
    )

    # Bucket bar chart — single code block, matches stats_blocks styling.
    bucket_lines = _bucket_bar_lines(summary.bucket_counts)
    if bucket_lines:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*By bucket*\n```\n" + "\n".join(bucket_lines) + "\n```",
                },
            }
        )

    if summary.rule_counts:
        from ..visualize import format_rule_name

        rules_text = "\n".join(
            f"• *{format_rule_name(name)}* × `{count}`"
            for name, count in summary.rule_counts[:5]
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Top rule hits:*\n{rules_text}"},
            }
        )

    if summary.notable_respond:
        items = "\n".join(
            f"• *{_truncate(it.subject, 80)}* — _{it.from_email}_ (conf `{it.confidence:.2f}`)"
            for it in summary.notable_respond
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Respond surfaced:*\n{items}"},
            }
        )

    if summary.notable_notify:
        items = "\n".join(
            f"• *{_truncate(it.subject, 80)}* — _{it.from_email}_" for it in summary.notable_notify
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Notify surfaced:*\n{items}"},
            }
        )

    footer_parts: list[str] = []
    if summary.uncertain_band:
        footer_parts.append(f"`{summary.uncertain_band}` uncertain auto-marks — `/mail review`")
    if summary.corrections_recent:
        footer_parts.append(f"`{summary.corrections_recent}` corrections logged")
    if footer_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(footer_parts)}],
            }
        )
    return blocks


def review_blocks(candidates: list[dict]) -> list[Block]:
    """Review batch: auto-marked-ignore mails where LLM was uncertain.

    Silence = neutral. Only logs when user explicitly corrects."""
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🔎 Review · {len(candidates)} uncertain auto-marks",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "_Silence = no signal. Tap Wrong bucket if the agent got it wrong._",
                }
            ],
        },
    ]
    for c in candidates:
        sender = c.get("from_email") or "(unknown)"
        subject = c.get("subject") or "(no subject)"
        conf = c.get("confidence", 0.0)
        source_key = c.get("source", "llm_fast")
        src_label = {
            "header_rule": "Rule", "llm_fast": "LLM", "llm_smart": "LLM", "user_correction": "User",
        }.get(source_key, source_key)
        from ..visualize import format_rule_name as _fmt_rule

        rule = _fmt_rule(c.get("rule_name")) if c.get("rule_name") else "-"
        account = c.get("account", "")
        message_id = c.get("message_id", "")
        thread_id = message_id  # fallback; Gmail accepts message id in URL
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{_truncate(subject, 120)}*  ·  _{sender}_\n"
                        f"`ignore` (auto-marked) · {src_label} · conf `{conf:.2f}` · `{rule}`"
                    ),
                },
            }
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open"},
                        "url": _gmail_url(thread_id),
                        "action_id": "open_gmail",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Wrong bucket"},
                        "value": _correct_value(account, message_id, "ignore"),
                        "action_id": "correct_bucket",
                    },
                ],
            }
        )
    return blocks


def _compact_row_text(email, decision, *, show_account: bool = False) -> str:
    """Sender-first compact row: headline + snippet + metadata context line."""
    from ..visualize import format_rule_name
    from .format import clean_snippet, clean_subject, relative_time

    sender = email.from_name or email.from_email
    rule_or_src = (
        format_rule_name(decision.rule_name) if decision.rule_name else _friendly_source(decision)
    )
    meta_parts = []
    if show_account:
        meta_parts.append(f"📬 {email.account}")
    meta_parts.append(f"🕒 {relative_time(email.received_at)}")
    meta_parts.append(decision.bucket.value)
    meta_parts.append(f"{rule_or_src} · conf {decision.confidence:.2f}")
    meta_line = " · ".join(meta_parts)
    return (
        f"*{sender}*  ·  {_truncate(clean_subject(email.subject), 60)}\n"
        f"> {_truncate(clean_snippet(email.snippet), 100)}\n"
        f"{meta_line}"
    )


# Slack hard-caps messages at 50 blocks; each row = 2 blocks + 1 header reserved.
_DIGEST_ROWS_PER_MESSAGE = 24


def grouped_row_blocks(group, *, show_account: bool = False) -> list[Block]:
    """Collapsed card for a thread group (≥2 sharing thread_id) or a subject
    group (≥3 sharing normalized subject). Account+bucket always shared. The
    `group.grouped_by` discriminator selects the context marker
    (`🧵 thread · N messages` vs `📦 N similar`)."""
    from .format import clean_snippet, clean_subject, relative_time

    rep = group.representative
    email = rep.email
    decision = rep.decision
    n = len(group.members)

    unique_senders = list(dict.fromkeys(
        r.email.from_name or r.email.from_email for r in group.members
    ))
    sender_display = f"{unique_senders[0]} +{len(unique_senders) - 1}" if len(unique_senders) > 1 else unique_senders[0]

    blocks: list[Block] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{sender_display}*  ·  {_truncate(clean_subject(email.subject), 60)}",
            },
        },
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {
                                    "type": "text",
                                    "text": f"{r.email.from_name or r.email.from_email}: "
                                    f"{_truncate(clean_snippet(r.email.snippet), 80)}  ",
                                    "style": {"italic": True},
                                },
                                {
                                    "type": "link",
                                    "url": _gmail_url(r.email.thread_id),
                                    "text": "↗",
                                },
                            ],
                        }
                        for r in group.members
                    ],
                }
            ],
        },
    ]

    meta_parts = []
    if show_account:
        meta_parts.append(f"📬 {email.account}")
    meta_parts.append(
        f"🧵 thread · {n} messages"
        if group.grouped_by == "thread"
        else f"📦 {n} similar"
    )
    oldest = min(r.email.received_at for r in group.members)
    newest = max(r.email.received_at for r in group.members)
    meta_parts.append(f"oldest {relative_time(oldest)} · newest {relative_time(newest)}")
    meta_parts.append(f"{decision.bucket.value} · conf {decision.confidence:.2f}")
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": " · ".join(meta_parts)}],
    })

    mark_all_value = ",".join(f"{r.email.account}:{r.email.id}" for r in group.members)
    if len(mark_all_value) > 1900:
        mark_all_value = f"{rep.email.account}:{rep.email.id}"

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"Mark all read ({n})"},
                "style": "danger",
                "value": mark_all_value,
                "action_id": "mark_read_bulk",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open latest"},
                "url": _gmail_url(rep.email.thread_id),
                "action_id": "open_gmail",
            },
            {
                "type": "overflow",
                "action_id": "row_overflow",
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Wrong bucket"},
                        "value": _correct_value(rep.email.account, rep.email.id, decision.bucket.value),
                    },
                ],
            },
        ],
    })
    return blocks


def digest_blocks(results: list[SurfaceResult], *, show_account: bool = False) -> list[Block]:
    """Batched notify-bucket mails. Runs `group_results` first: items sharing
    a Gmail thread_id collapse at ≥2; items sharing only a normalized subject
    collapse at ≥3. Worst case: 24 items all in 2-item thread groups →
    12×4 + 1 header = 49 blocks (< 50 cap)."""
    from .format import ResultGroup, group_results

    grouped = group_results(results)
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📬 Digest · {len(results)}"},
        }
    ]
    for item in grouped:
        if isinstance(item, ResultGroup):
            blocks.extend(grouped_row_blocks(item, show_account=show_account))
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _compact_row_text(item.email, item.decision, show_account=show_account),
                },
            })
            blocks.append(_row_actions(item.email, item.decision.bucket.value))
    return blocks


def split_digest(results: list[SurfaceResult]) -> list[list[SurfaceResult]]:
    """Chunk results so each chunk renders to ≤50 blocks via digest_blocks."""
    return [
        results[i : i + _DIGEST_ROWS_PER_MESSAGE]
        for i in range(0, len(results), _DIGEST_ROWS_PER_MESSAGE)
    ]


def ask_result_blocks(question: str, answer) -> list[Block]:
    """Render an AnalysisAnswer as a Slack message."""
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔎 {_truncate(question, 150)}"},
        },
        {"type": "divider"},
    ]

    # Slack section blocks cap at 3 000 chars — split answer if needed
    raw = f"*{answer.answer}*" if answer.format_hint == "number" else answer.answer
    text = _md_to_mrkdwn(raw)
    chunk_size = 2_900
    for i in range(0, max(1, len(text)), chunk_size):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text[i : i + chunk_size]},
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_Based on {answer.source_count} email(s)_"}
            ],
        }
    )
    return blocks


def uncertain_ignore_blocks(
    results: list[SurfaceResult], *, show_account: bool = False
) -> list[Block]:
    """Ignore-bucket SurfaceResults — mails the auto-mark gate refused
    (low confidence or no opt-in rule). Surfaced for human review so they
    don't silently disappear into `processed_messages`."""
    from .format import ResultGroup, group_results

    grouped = group_results(results)
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"⚠️ Uncertain · {len(results)}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "_Auto-archive blocked by confidence floor. "
                        "Confirm with Mark read, or fix via Wrong bucket._"
                    ),
                }
            ],
        },
    ]
    for item in grouped:
        if isinstance(item, ResultGroup):
            blocks.extend(grouped_row_blocks(item, show_account=show_account))
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _compact_row_text(item.email, item.decision, show_account=show_account),
                },
            })
            blocks.append(_row_actions(item.email, item.decision.bucket.value))
    return blocks


_AUDIT_SOURCE_LABELS = {
    "header_rule": "Rule",
    "llm_fast": "LLM",
    "llm_smart": "LLM",
    "user_correction": "User",
}


def _audit_sender_display(from_email: str) -> str:
    """Best-effort human name from an email's local part.

    `olesia.pshenychna@makeheadway.com` → `Olesia Pshenychna`.
    Plus-tags and digits are stripped. Falls back to the raw email if nothing
    usable remains.
    """
    if not from_email:
        return "(unknown)"
    local = from_email.split("@", 1)[0]
    local = local.split("+", 1)[0]
    cleaned = re.sub(r"[._\-]+", " ", local).strip()
    parts = [p for p in cleaned.split() if not p.isdigit()]
    if not parts:
        return from_email
    return " ".join(p.capitalize() for p in parts)


_RECENT_COL_TIME = 9
_RECENT_COL_SENDER = 18
_RECENT_COL_SUBJECT = 34
_RECENT_COL_SOURCE = 14


def _pad_col(s: str, width: int) -> str:
    """Truncate-with-ellipsis to width then left-pad spaces. ASCII columns only."""
    if len(s) > width:
        s = s[: max(0, width - 1)] + "…"
    return s.ljust(width)


def _audit_source_label(entry) -> str:
    """Source column text. Prefer the formatted rule name when a header rule
    matched (more informative than the generic 'Rule'); otherwise the friendly
    source label."""
    if entry.source == "header_rule" and entry.rule_name:
        from ..visualize import format_rule_name

        return format_rule_name(entry.rule_name)
    return _AUDIT_SOURCE_LABELS.get(entry.source, entry.source or "—")


def recent_blocks(entries) -> list[Block]:
    """Audit log for the last N mark-read actions, rendered as a monospace
    table inside a `rich_text_preformatted` block.

    Visible cap of 24 rows keeps the message under Slack's 50-block limit and
    keeps the table itself under the per-block character limit.
    """
    from datetime import datetime

    from .format import clean_subject, relative_time

    visible = list(entries)[:24]
    n = len(visible)
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🧹 Mark-read · last {n}"},
        }
    ]
    if not visible:
        return blocks

    lines = [
        "  ".join(
            (
                _pad_col("Time", _RECENT_COL_TIME),
                _pad_col("Sender", _RECENT_COL_SENDER),
                _pad_col("Subject", _RECENT_COL_SUBJECT),
                _pad_col("Source", _RECENT_COL_SOURCE),
                "Conf",
            )
        )
    ]

    for e in visible:
        sender = _audit_sender_display(e.from_email)
        subject = clean_subject(e.subject) if e.subject else "(no subject)"

        try:
            dt = datetime.fromisoformat(e.marked_at)
            time_str = relative_time(dt)
        except (ValueError, TypeError):
            time_str = e.marked_at or "—"

        src = _audit_source_label(e)
        if e.dry_run:
            src = f"{src}*"  # trailing * marks dry-run rows

        lines.append(
            "  ".join(
                (
                    _pad_col(time_str, _RECENT_COL_TIME),
                    _pad_col(sender, _RECENT_COL_SENDER),
                    _pad_col(subject, _RECENT_COL_SUBJECT),
                    _pad_col(src, _RECENT_COL_SOURCE),
                    f"{e.confidence:.2f}",
                )
            )
        )

    table_text = "\n".join(lines)
    blocks.append(
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": table_text}],
                }
            ],
        }
    )

    has_dry = any(e.dry_run for e in visible)
    footer_parts: list[str] = []
    if has_dry:
        footer_parts.append("_`*` = dry-run._")
    if len(entries) > 24:
        footer_parts.append(
            f"_…and {len(entries) - 24} more (raise the limit to see them)._"
        )
    if footer_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "  ".join(footer_parts)}],
            }
        )

    return blocks

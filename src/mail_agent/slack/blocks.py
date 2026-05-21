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


def _source_label(decision: TriageDecision) -> str:
    """Prefer concrete model ID when available, else source name."""
    if decision.model:
        return decision.model
    return decision.source


def _decision_context(decision: TriageDecision, prefix: str = "Why") -> dict:
    """Multi-line mrkdwn — each bullet on its own line for readability."""
    lines = [
        f"*{prefix}:* _{decision.reasoning}_",
        f"• Model: `{_source_label(decision)}`",
        f"• Confidence: `{decision.confidence:.2f}`",
    ]
    if decision.rule_name:
        from ..visualize import format_rule_name

        lines.append(f"• Rule: *{format_rule_name(decision.rule_name)}*")
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(lines)}]}


def respond_blocks(result: SurfaceResult) -> list[Block]:
    """Single high-priority mail. One message per respond-bucket result."""
    email = result.email
    decision = result.decision
    sender = f"{email.from_name} <{email.from_email}>" if email.from_name else email.from_email
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔔 Needs response"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{email.subject}*\n_From: {sender}_\n\n>{_truncate(email.snippet, 350)}",
            },
        },
        _decision_context(decision, prefix="Why respond"),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Mark read"},
                    "style": "danger",
                    "value": _mark_read_value(email.account, email.id),
                    "action_id": "mark_read",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open in Gmail"},
                    "url": _gmail_url(email.thread_id),
                    "action_id": "open_gmail",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Wrong bucket"},
                    "value": _correct_value(email.account, email.id, decision.bucket.value),
                    "action_id": "correct_bucket",
                },
            ],
        },
    ]


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
    nl_query: str, gmail_query: str, results: list, reasoning: str = ""
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
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"`{r.received_at.strftime('%Y-%m-%d %H:%M')}` · "
                            f"<{_gmail_url(r.thread_id)}|Open in Gmail>"
                        ),
                    }
                ],
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
        model = c.get("model") or c.get("source", "llm_fast")
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
                        f"`ignore` (auto-marked) · `{model}` · conf `{conf:.2f}` · `{rule}`"
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


def _row_buttons(email, original_bucket: str) -> dict:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Mark read"},
                "value": _mark_read_value(email.account, email.id),
                "action_id": "mark_read",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open"},
                "url": _gmail_url(email.thread_id),
                "action_id": "open_gmail",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Wrong bucket"},
                "value": _correct_value(email.account, email.id, original_bucket),
                "action_id": "correct_bucket",
            },
        ],
    }


def _compact_row_text(email, decision) -> str:
    """One mrkdwn blob: subject, sender, snippet, classifier meta. Used in
    digest/review to keep each item to 2 blocks (section + actions) and stay
    under Slack's 50-block message limit."""
    from ..visualize import format_rule_name

    sender = email.from_name or email.from_email
    rule_or_src = (
        format_rule_name(decision.rule_name) if decision.rule_name else _source_label(decision)
    )
    return (
        f"*{_truncate(email.subject, 120)}*  ·  _{sender}_\n"
        f"> {_truncate(email.snippet, 200)}\n"
        f"`{decision.bucket.value}` · {rule_or_src} · conf `{decision.confidence:.2f}`"
    )


# Slack hard-caps messages at 50 blocks; each row = 2 blocks + 1 header reserved.
_DIGEST_ROWS_PER_MESSAGE = 24


def digest_blocks(results: list[SurfaceResult]) -> list[Block]:
    """Batched notify-bucket mails. 2 blocks per row (section + actions) so
    we fit ~24 items in one message. Caller splits long batches across
    multiple posts via :func:`split_digest`."""
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📬 Digest · {len(results)} item(s)"},
        }
    ]
    for r in results:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _compact_row_text(r.email, r.decision)},
            }
        )
        blocks.append(_row_buttons(r.email, r.decision.bucket.value))
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


def uncertain_ignore_blocks(results: list[SurfaceResult]) -> list[Block]:
    """Ignore-bucket SurfaceResults — mails the auto-mark gate refused
    (low confidence or no opt-in rule). Surfaced for human review so they
    don't silently disappear into `processed_messages`."""
    blocks: list[Block] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"⚠️ Uncertain auto-marks · {len(results)} item(s)",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "_Classified `ignore` but gate held — confirm with Mark read, "
                        "or correct via Wrong bucket._"
                    ),
                }
            ],
        },
    ]
    for r in results:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _compact_row_text(r.email, r.decision)},
            }
        )
        blocks.append(_row_buttons(r.email, r.decision.bucket.value))
    return blocks

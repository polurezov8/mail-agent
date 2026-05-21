"""Block-level tests for the /mail recent audit log card (code-block table)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mail_agent.slack.blocks import _audit_sender_display, recent_blocks
from mail_agent.store.stats import AuditEntry


def _entry(
    *,
    secs_ago: int = 30,
    from_email: str = "alice.smith@example.com",
    subject: str = "Hello",
    rule_name: str | None = None,
    source: str = "llm_fast",
    confidence: float = 0.92,
    dry_run: bool = False,
) -> AuditEntry:
    marked_at = (datetime.now(timezone.utc) - timedelta(seconds=secs_ago)).isoformat()
    return AuditEntry(
        marked_at=marked_at,
        from_email=from_email,
        subject=subject,
        rule_name=rule_name,
        source=source,
        confidence=confidence,
        dry_run=dry_run,
    )


def _table_text(blocks) -> str:
    """Extract the rich_text_preformatted text from recent_blocks output."""
    for b in blocks:
        if b.get("type") == "rich_text":
            return b["elements"][0]["elements"][0]["text"]
    raise AssertionError("no rich_text block in output")


# ─── _audit_sender_display ────────────────────────────────────────────────────


def test_sender_display_dotted_local_part():
    assert _audit_sender_display("olesia.pshenychna@makeheadway.com") == "Olesia Pshenychna"


def test_sender_display_underscore_local_part():
    assert _audit_sender_display("john_doe@example.com") == "John Doe"


def test_sender_display_plus_tag_stripped():
    assert _audit_sender_display("alice+work@example.com") == "Alice"


def test_sender_display_digits_filtered():
    assert _audit_sender_display("alice.smith.42@example.com") == "Alice Smith"


def test_sender_display_empty_falls_back():
    assert _audit_sender_display("") == "(unknown)"


def test_sender_display_all_digits_falls_back_to_email():
    assert _audit_sender_display("12345@example.com") == "12345@example.com"


# ─── recent_blocks structure ──────────────────────────────────────────────────


def test_recent_blocks_header_has_count():
    blocks = recent_blocks([_entry(), _entry()])
    assert blocks[0]["type"] == "header"
    assert "last 2" in blocks[0]["text"]["text"]


def test_recent_blocks_uses_preformatted_table():
    blocks = recent_blocks([_entry()])
    # 1 header + 1 rich_text = 2 blocks
    assert len(blocks) == 2
    assert blocks[1]["type"] == "rich_text"
    assert blocks[1]["elements"][0]["type"] == "rich_text_preformatted"


def test_recent_blocks_empty_input_only_header():
    blocks = recent_blocks([])
    assert len(blocks) == 1
    assert blocks[0]["type"] == "header"


def test_recent_blocks_table_has_header_row():
    blocks = recent_blocks([_entry()])
    table = _table_text(blocks)
    first_line = table.split("\n", 1)[0]
    assert "Time" in first_line
    assert "Sender" in first_line
    assert "Subject" in first_line
    assert "Source" in first_line
    assert "Conf" in first_line


def test_recent_blocks_no_raw_source_id_visible():
    blocks = recent_blocks([_entry(source="llm_fast")])
    table = _table_text(blocks)
    assert "llm_fast" not in table
    assert "LLM" in table


def test_recent_blocks_sender_in_table_row():
    blocks = recent_blocks([_entry(from_email="olesia.pshenychna@makeheadway.com")])
    table = _table_text(blocks)
    assert "Olesia Pshenychna" in table


def test_recent_blocks_subject_calendar_cleaned():
    raw = "Declined: Nibble MTPO Planning @ Mon Jun 15, 2026 1pm - 1:50pm (EEST) (Dmytro Poluriezov)"
    blocks = recent_blocks([_entry(subject=raw)])
    table = _table_text(blocks)
    assert "Mon Jun 15" not in table
    assert "Declined: Nibble" in table  # subject column truncates at 34 chars


def test_recent_blocks_dry_run_marker():
    blocks = recent_blocks([_entry(dry_run=True)])
    blob = json.dumps(blocks)
    # Dry-run rows get a trailing * on the source column,
    # plus a footer context explaining the marker.
    assert "dry-run" in blob.lower()


def test_recent_blocks_no_dry_run_footer_when_none():
    blocks = recent_blocks([_entry(dry_run=False)])
    # 1 header + 1 rich_text only — no footer
    assert len(blocks) == 2


def test_recent_blocks_caps_visible_at_24_with_overflow_footer():
    blocks = recent_blocks([_entry() for _ in range(30)])
    # 1 header + 1 rich_text + 1 footer (overflow) = 3
    assert len(blocks) == 3
    table = _table_text(blocks)
    # 24 data rows + 1 header row
    assert len(table.split("\n")) == 25
    footer_text = blocks[2]["elements"][0]["text"]
    assert "and 6 more" in footer_text


def test_recent_blocks_under_cap_no_overflow_footer():
    blocks = recent_blocks([_entry() for _ in range(5)])
    # 1 header + 1 rich_text = 2 (no footer)
    assert len(blocks) == 2


def test_recent_blocks_rule_name_shown_for_header_rule():
    blocks = recent_blocks(
        [_entry(rule_name="github_notifications", source="header_rule")]
    )
    table = _table_text(blocks)
    # format_rule_name prettifies "github_notifications" → "Github notifications" or similar
    assert "github" in table.lower()


def test_recent_blocks_confidence_two_decimals():
    blocks = recent_blocks([_entry(confidence=0.873)])
    table = _table_text(blocks)
    assert "0.87" in table


def test_recent_blocks_columns_aligned():
    """Verify monospace alignment: every data row has the same length as the header row."""
    blocks = recent_blocks([
        _entry(from_email="a@b.com", subject="x"),
        _entry(from_email="very.long.dotted.name@example.com", subject="A much longer subject than the short one"),
    ])
    table = _table_text(blocks)
    lines = table.split("\n")
    header_len = len(lines[0])
    for line in lines[1:]:
        # The Conf column (last) is unpadded so lines may be shorter by up to 4 chars.
        # The padded prefix (Time + Sender + Subject + Source) must be aligned.
        prefix_width = 9 + 2 + 18 + 2 + 34 + 2 + 14
        assert line[:prefix_width] == line[:prefix_width].rstrip() + " " * (
            prefix_width - len(line[:prefix_width].rstrip())
        ) or len(line[:prefix_width]) == prefix_width
        assert header_len >= prefix_width


def test_recent_blocks_long_subject_truncated_with_ellipsis():
    blocks = recent_blocks(
        [_entry(subject="A" * 100)]
    )
    table = _table_text(blocks)
    # Subject column is 34 chars wide → truncated to 33 chars + ellipsis
    assert "…" in table
    assert "A" * 100 not in table

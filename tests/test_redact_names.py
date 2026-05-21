from __future__ import annotations

from mail_agent.redact import hydrate, redact_text


def test_full_name_redacted_to_single_placeholder():
    red, mapping = redact_text(
        "Hi Jane Doe, please review.", names=["Jane Doe"]
    )
    assert "Jane Doe" not in red
    assert "<PERSON_1>" in red
    assert mapping["<PERSON_1>"] == "Jane Doe"


def test_first_and_last_name_both_collapse_to_same_placeholder():
    red, mapping = redact_text(
        "Jane asked. Later, Doe replied. Then Jane Doe closed.",
        names=["Jane Doe"],
    )
    # All three references must point at the same person.
    assert red.count("<PERSON_1>") == 3
    assert mapping == {"<PERSON_1>": "Jane Doe"}


def test_name_match_is_case_insensitive():
    red, _ = redact_text("jane will join", names=["Jane Doe"])
    assert "<PERSON_1>" in red
    assert "jane" not in red.lower().replace("<person_1>", "")


def test_multiple_people_get_distinct_placeholders():
    red, mapping = redact_text(
        "Jane met Alice yesterday.", names=["Jane Doe", "Alice Liddell"]
    )
    assert "<PERSON_1>" in red and "<PERSON_2>" in red
    assert mapping["<PERSON_1>"] == "Jane Doe"
    assert mapping["<PERSON_2>"] == "Alice Liddell"


def test_word_boundary_prevents_substring_matches():
    # "Alex" should NOT match inside "Alexandra"
    red, _ = redact_text("Alexandra wrote.", names=["Alex"])
    assert red == "Alexandra wrote."


def test_short_parts_skipped():
    # "Jo Li" → 2-letter parts both skipped; only the full name participates.
    red, mapping = redact_text("Jo Li and Jo separately.", names=["Jo Li"])
    assert "<PERSON_1>" in red  # full name matches
    # 'Jo' standalone NOT replaced (length < 3)
    assert "Jo separately" in red


def test_name_does_not_swallow_email_part():
    # Email pattern runs AFTER names; "Jane" inside "jane@..." is part of
    # the email and the URL/EMAIL regex should win on the full address.
    red, mapping = redact_text(
        "Email jane@example.com to Jane Doe.",
        names=["Jane Doe"],
    )
    assert "jane@example.com" not in red
    assert "<PERSON_1>" in red
    # Email got its own placeholder
    assert any(k.startswith("<EMAIL_") for k in mapping)


def test_hydrate_restores_canonical_form_for_any_variant():
    original_with_variants = "Jane and Doe; full: Jane Doe"
    red, mapping = redact_text(original_with_variants, names=["Jane Doe"])
    restored = hydrate(red, mapping)
    # All three references now read as the canonical full name — that's the
    # intended behavior (LLM reasoning becomes consistent on output).
    assert restored == "Jane Doe and Jane Doe; full: Jane Doe"


def test_unused_name_not_added_to_mapping():
    red, mapping = redact_text("Just a sentence.", names=["Bob Smith"])
    assert red == "Just a sentence."
    assert mapping == {}


def test_no_names_list_behaves_like_before():
    red, _ = redact_text("Jane Doe wrote.", names=None)
    assert "Jane Doe" in red  # not redacted without an explicit list

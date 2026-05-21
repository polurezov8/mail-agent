from __future__ import annotations

from mail_agent.redact import hydrate, redact_text


def test_redact_email():
    s = "Please contact alice@example.com for details."
    red, mapping = redact_text(s)
    assert "alice@example.com" not in red
    assert "<EMAIL_1>" in red
    assert mapping["<EMAIL_1>"] == "alice@example.com"


def test_redact_url():
    s = "Visit https://example.com/path?q=1 to reset."
    red, mapping = redact_text(s)
    assert "https://example.com/path?q=1" not in red
    assert "<URL_1>" in red


def test_redact_iban():
    s = "Wire to DE89370400440532013000 by Friday."
    red, mapping = redact_text(s)
    assert "DE89370400440532013000" not in red
    assert any(k.startswith("<IBAN_") for k in mapping)


def test_redact_credit_card_shaped():
    s = "Card number 4111 1111 1111 1111 was charged."
    red, mapping = redact_text(s)
    assert "4111 1111 1111 1111" not in red
    assert any(k.startswith("<CC_") for k in mapping)


def test_redact_phone():
    s = "Call +1 415-555-1212 if urgent."
    red, mapping = redact_text(s)
    assert "415-555-1212" not in red
    assert any(k.startswith("<PHONE_") for k in mapping)


def test_redact_same_value_reuses_placeholder():
    s = "alice@example.com sent it. cc alice@example.com again."
    red, mapping = redact_text(s)
    assert red.count("<EMAIL_1>") == 2
    assert len([k for k in mapping if k.startswith("<EMAIL_")]) == 1


def test_redact_different_values_get_distinct_placeholders():
    s = "From alice@x.com To bob@y.com"
    red, mapping = redact_text(s)
    assert "<EMAIL_1>" in red and "<EMAIL_2>" in red
    assert set(mapping.values()) == {"alice@x.com", "bob@y.com"}


def test_hydrate_restores_original():
    original = "Email: alice@example.com or call +1 415-555-1212"
    red, mapping = redact_text(original)
    restored = hydrate(red, mapping)
    assert restored == original


def test_hydrate_handles_two_digit_placeholders():
    # Force >9 emails so we get <EMAIL_10>; ensure ordering doesn't break.
    emails = " ".join(f"u{i}@example.com" for i in range(12))
    red, mapping = redact_text(emails)
    restored = hydrate(red, mapping)
    assert restored == emails


def test_redact_empty_input():
    red, mapping = redact_text("")
    assert red == ""
    assert mapping == {}


def test_redact_url_eats_email_inside():
    s = "Open https://example.com/u@host.com/done now."
    red, mapping = redact_text(s)
    # URL pattern should swallow the whole URL including the @-fragment
    assert "https://example.com/u@host.com/done" not in red
    # No bare EMAIL placeholder should escape from inside the URL
    assert not any(v == "u@host.com" for v in mapping.values())

"""Tests for gmail.client.fetch_message_body."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock


from mail_agent.gmail.client import _extract_text_from_payload, fetch_message_body


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _plain_payload(text: str) -> dict:
    return {"mimeType": "text/plain", "body": {"data": _b64(text)}}


def _html_payload(text: str) -> dict:
    return {"mimeType": "text/html", "body": {"data": _b64(text)}}


def _multipart(parts: list[dict]) -> dict:
    return {"mimeType": "multipart/alternative", "body": {}, "parts": parts}


# ── _extract_text_from_payload ─────────────────────────────────────────────────

def test_extract_plain_text():
    payload = _plain_payload("Hello world")
    assert _extract_text_from_payload(payload) == "Hello world"


def test_extract_prefers_plain_over_html():
    payload = _multipart([_html_payload("<b>HTML</b>"), _plain_payload("plain text")])
    result = _extract_text_from_payload(payload)
    assert "plain text" in result


def test_extract_truncates_at_8000(monkeypatch):
    long_text = "x" * 10_000
    payload = _plain_payload(long_text)
    result = _extract_text_from_payload(payload)
    # _extract_text_from_payload doesn't truncate — fetch_message_body does
    assert len(result) == 10_000  # raw extraction returns full text


# ── fetch_message_body ─────────────────────────────────────────────────────────

def _mock_account():
    acct = MagicMock()
    acct.name = "personal"
    return acct


def test_fetch_message_body_returns_plain_text(monkeypatch):
    text = "This is the email body."
    raw_response = {"payload": _plain_payload(text)}
    svc = MagicMock()
    svc.users().messages().get().execute.return_value = raw_response
    monkeypatch.setattr("mail_agent.gmail.client._service", lambda acct: svc)

    result = fetch_message_body(_mock_account(), "msg-001")
    assert result == text


def test_fetch_message_body_truncates_at_8000(monkeypatch):
    long_text = "y" * 10_000
    raw_response = {"payload": _plain_payload(long_text)}
    svc = MagicMock()
    svc.users().messages().get().execute.return_value = raw_response
    monkeypatch.setattr("mail_agent.gmail.client._service", lambda acct: svc)

    result = fetch_message_body(_mock_account(), "msg-002")
    assert len(result) == 8_000


def test_fetch_message_body_falls_back_to_html(monkeypatch):
    payload = _multipart([_html_payload("html body content")])
    raw_response = {"payload": payload}
    svc = MagicMock()
    svc.users().messages().get().execute.return_value = raw_response
    monkeypatch.setattr("mail_agent.gmail.client._service", lambda acct: svc)

    result = fetch_message_body(_mock_account(), "msg-003")
    assert "html body content" in result

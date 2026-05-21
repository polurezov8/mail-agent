from __future__ import annotations

from mail_agent.gmail.unsubscribe import (
    UnsubscribeOption,
    parse_list_unsubscribe,
    perform_unsubscribe,
)


def test_parse_one_click_post_preferred():
    headers = {
        "List-Unsubscribe": "<https://example.com/u?id=42>, <mailto:unsub@example.com>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    opt = parse_list_unsubscribe(headers)
    assert opt.method == "one_click_post"
    assert opt.url == "https://example.com/u?id=42"


def test_parse_prefers_https_over_http():
    headers = {
        "List-Unsubscribe": "<http://insecure.example.com/u>, <https://secure.example.com/u>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    opt = parse_list_unsubscribe(headers)
    assert opt.url == "https://secure.example.com/u"


def test_parse_mailto_fallback_when_no_one_click():
    headers = {"List-Unsubscribe": "<mailto:unsub@example.com>"}
    opt = parse_list_unsubscribe(headers)
    assert opt.method == "mailto"
    assert opt.mailto == "unsub@example.com"


def test_parse_returns_none_when_no_header():
    opt = parse_list_unsubscribe({})
    assert opt.method == "none"


def test_perform_unsubscribe_one_click_success(monkeypatch):
    import mail_agent.gmail.unsubscribe as mod

    class _Resp:
        status_code = 200

    def fake_post(url, data=None, timeout=None, follow_redirects=None):
        assert data == {"List-Unsubscribe": "One-Click"}
        return _Resp()

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    result = perform_unsubscribe(
        "sender@x.com",
        UnsubscribeOption(method="one_click_post", url="https://x.com/u"),
    )
    assert result.success
    assert result.method == "one_click_post"


def test_perform_unsubscribe_one_click_failure(monkeypatch):
    import mail_agent.gmail.unsubscribe as mod

    def fake_post(*a, **kw):
        raise Exception("connection refused")

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    result = perform_unsubscribe(
        "sender@x.com",
        UnsubscribeOption(method="one_click_post", url="https://x.com/u"),
    )
    assert not result.success
    assert "connection refused" in result.detail


def test_perform_unsubscribe_mailto_reports_manual():
    result = perform_unsubscribe(
        "sender@x.com",
        UnsubscribeOption(method="mailto", mailto="unsub@x.com"),
    )
    assert not result.success
    assert "manual" in result.detail.lower()


def test_perform_unsubscribe_none_returns_unsuccessful():
    result = perform_unsubscribe(
        "sender@x.com",
        UnsubscribeOption(method="none"),
    )
    assert not result.success
    assert result.method == "none"

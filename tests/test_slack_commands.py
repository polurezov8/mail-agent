from __future__ import annotations

from mail_agent.slack.commands import _pick_diverse, dispatch


class _RespondSpy:
    """Captures respond() calls."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


def test_pick_diverse_keeps_first_per_sender():
    candidates = [
        {"id": 1, "from_email": "a@x.com"},
        {"id": 2, "from_email": "a@x.com"},
        {"id": 3, "from_email": "b@x.com"},
        {"id": 4, "from_email": "c@x.com"},
    ]
    picked = _pick_diverse(candidates, n=5)
    assert [p["id"] for p in picked] == [1, 3, 4]


def test_pick_diverse_respects_n_limit():
    candidates = [{"id": i, "from_email": f"u{i}@x.com"} for i in range(10)]
    picked = _pick_diverse(candidates, n=3)
    assert len(picked) == 3


def test_dispatch_help_replies_with_help_text():
    respond = _RespondSpy()
    dispatch("help", respond)
    assert respond.calls
    assert "/mail" in respond.calls[0]["text"]


def test_dispatch_unknown_subcommand_replies_with_hint():
    respond = _RespondSpy()
    dispatch("frobnicate", respond)
    assert respond.calls
    assert "Unknown" in respond.calls[0]["text"]


def test_dispatch_corrections_no_action_lists():
    respond = _RespondSpy()
    dispatch("corrections", respond)
    # Either empty list message OR table — both fine; just verify a reply.
    assert respond.calls
    text = respond.calls[0]["text"]
    assert ("No corrections" in text) or ("Corrections" in text)


def test_dispatch_corrections_undo_missing_id_warns():
    respond = _RespondSpy()
    dispatch("corrections undo", respond)
    assert respond.calls
    assert "Usage" in respond.calls[0]["text"] or "ID" in respond.calls[0]["text"]


def test_dispatch_bare_mail_runs_triage(monkeypatch):
    """Bare `mail` DM behaves like `/mail` and `/mail triage` — triage, not analysis."""
    triaged = []
    asked = []
    monkeypatch.setattr("mail_agent.slack.commands.handle_triage", lambda respond: triaged.append(True))
    monkeypatch.setattr("mail_agent.slack.commands.handle_ask", lambda respond, q: asked.append(q))

    dispatch("mail", _RespondSpy(), fallback_to_search=True)
    assert triaged == [True]
    assert asked == []


def test_dispatch_mail_prefixed_nl_query_still_asks(monkeypatch):
    """`mail me the invoices` is an NL question, not a triage trigger."""
    triaged = []
    asked = []
    monkeypatch.setattr("mail_agent.slack.commands.handle_triage", lambda respond: triaged.append(True))
    monkeypatch.setattr("mail_agent.slack.commands.handle_ask", lambda respond, q: asked.append(q))

    dispatch("mail me the invoices", _RespondSpy(), fallback_to_search=True)
    assert triaged == []
    assert asked == ["mail me the invoices"]

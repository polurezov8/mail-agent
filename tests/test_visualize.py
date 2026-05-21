from __future__ import annotations

from mail_agent.visualize import hbar, percent, sparkline


def test_hbar_half():
    assert hbar(5, 10, width=10) == "█████░░░░░"


def test_hbar_full():
    assert hbar(10, 10, width=10) == "██████████"


def test_hbar_zero_total():
    assert hbar(5, 0) == ""


def test_hbar_value_caps_at_width():
    # value > total should clamp
    bar = hbar(20, 10, width=10)
    assert len(bar) == 10
    assert bar == "██████████"


def test_sparkline_empty():
    assert sparkline([]) == ""


def test_sparkline_monotonic():
    out = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
    assert len(out) == 8
    # last char should be the max bar
    assert out[-1] == "█"
    assert out[0] != "█"


def test_percent_zero_total():
    assert percent(0, 0) == "  —"


def test_percent_value():
    assert percent(50, 200).strip() == "25%"

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mail_agent.schedule.types import CronSchedule, CronWindow, Weekday


def test_weekday_uses_launchd_numbering():
    assert int(Weekday.SUN) == 0
    assert int(Weekday.MON) == 1
    assert int(Weekday.TUE) == 2
    assert int(Weekday.WED) == 3
    assert int(Weekday.THU) == 4
    assert int(Weekday.FRI) == 5
    assert int(Weekday.SAT) == 6


def test_weekday_is_sortable():
    days = {Weekday.FRI, Weekday.MON, Weekday.SUN}
    assert sorted(days) == [Weekday.SUN, Weekday.MON, Weekday.FRI]


def _weekday_set(*days: Weekday) -> frozenset[Weekday]:
    return frozenset(days)


def test_cron_window_valid_construction():
    w = CronWindow(
        weekdays=_weekday_set(Weekday.MON, Weekday.TUE),
        hours=frozenset({10, 12}),
    )
    assert w.minute == 0
    assert w.weekdays == _weekday_set(Weekday.MON, Weekday.TUE)
    assert w.hours == frozenset({10, 12})


def test_cron_window_rejects_empty_weekdays():
    with pytest.raises(ValidationError):
        CronWindow(weekdays=frozenset(), hours=frozenset({10}))


def test_cron_window_rejects_empty_hours():
    with pytest.raises(ValidationError):
        CronWindow(weekdays=_weekday_set(Weekday.MON), hours=frozenset())


def test_cron_window_rejects_hour_below_zero():
    with pytest.raises(ValidationError):
        CronWindow(weekdays=_weekday_set(Weekday.MON), hours=frozenset({-1}))


def test_cron_window_rejects_hour_above_23():
    with pytest.raises(ValidationError):
        CronWindow(weekdays=_weekday_set(Weekday.MON), hours=frozenset({24}))


def test_cron_window_rejects_invalid_minute():
    with pytest.raises(ValidationError):
        CronWindow(
            weekdays=_weekday_set(Weekday.MON),
            hours=frozenset({10}),
            minute=60,
        )


def test_cron_schedule_kind_discriminator():
    s = CronSchedule(
        windows=(
            CronWindow(
                weekdays=_weekday_set(Weekday.MON),
                hours=frozenset({10}),
            ),
        )
    )
    assert s.kind == "cron"


def test_cron_schedule_rejects_empty_windows():
    with pytest.raises(ValidationError):
        CronSchedule(windows=())


def test_cron_schedule_accepts_multiple_windows():
    s = CronSchedule(
        windows=(
            CronWindow(
                weekdays=_weekday_set(Weekday.MON, Weekday.TUE),
                hours=frozenset({10, 12}),
            ),
            CronWindow(
                weekdays=_weekday_set(Weekday.SAT, Weekday.SUN),
                hours=frozenset({10}),
            ),
        )
    )
    assert len(s.windows) == 2

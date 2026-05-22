from __future__ import annotations

from pathlib import Path

from mail_agent.schedule.jobs import default_jobs
from mail_agent.schedule.types import (
    CronSchedule,
    DailySchedule,
    KeepAliveSchedule,
    Weekday,
)


def _jobs(tmp_path: Path, include_listener: bool = True, include_backup: bool = True):
    return default_jobs(
        project_root=tmp_path,
        uv_bin=tmp_path / "uv",
        include_listener=include_listener,
        include_backup=include_backup,
    )


def test_default_jobs_emits_single_triage_cron(tmp_path):
    jobs = _jobs(tmp_path, include_listener=False, include_backup=False)
    assert [j.name for j in jobs] == ["mail-agent.triage-cron"]


def test_default_jobs_drops_poll_and_daily(tmp_path):
    names = {j.name for j in _jobs(tmp_path)}
    assert "mail-agent.triage-poll" not in names
    assert "mail-agent.triage-daily" not in names


def test_default_jobs_includes_listener(tmp_path):
    names = {j.name for j in _jobs(tmp_path, include_listener=True)}
    assert "mail-agent.slack-listener" in names


def test_default_jobs_includes_backup(tmp_path):
    names = {j.name for j in _jobs(tmp_path, include_backup=True)}
    assert "mail-agent.backup" in names


def test_default_jobs_can_skip_backup(tmp_path):
    names = {j.name for j in _jobs(tmp_path, include_backup=False)}
    assert "mail-agent.backup" not in names


def test_backup_is_daily(tmp_path):
    jobs = _jobs(tmp_path, include_listener=False, include_backup=True)
    backup = next(j for j in jobs if j.name == "mail-agent.backup")
    assert isinstance(backup.schedule, DailySchedule)
    assert backup.command[-1] == "backup"


def test_triage_cron_uses_workday_and_weekend_windows(tmp_path):
    jobs = _jobs(tmp_path, include_listener=False)
    triage = next(j for j in jobs if j.name == "mail-agent.triage-cron")
    assert isinstance(triage.schedule, CronSchedule)
    assert len(triage.schedule.windows) == 2
    weekday_window, weekend_window = triage.schedule.windows
    assert weekday_window.weekdays == frozenset({
        Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI,
    })
    assert weekday_window.hours == frozenset({10, 12, 14, 16, 18})
    assert weekend_window.weekdays == frozenset({Weekday.SAT, Weekday.SUN})
    assert weekend_window.hours == frozenset({10, 18})


def test_listener_is_keep_alive(tmp_path):
    jobs = _jobs(tmp_path, include_listener=True)
    listener = next(j for j in jobs if j.name == "mail-agent.slack-listener")
    assert isinstance(listener.schedule, KeepAliveSchedule)

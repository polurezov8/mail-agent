from __future__ import annotations

import plistlib

from mail_agent.schedule.launchd import LaunchdScheduler
from mail_agent.schedule.types import (
    DailySchedule,
    IntervalSchedule,
    JobSpec,
    KeepAliveSchedule,
)


def _job(name, schedule, tmp_path) -> JobSpec:
    return JobSpec(
        name=name,
        description="test job",
        command=["/usr/bin/true"],
        schedule=schedule,
        working_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )


def test_interval_plist_has_start_interval(tmp_path):
    sched = LaunchdScheduler(agents_dir=tmp_path / "agents")
    job = _job("mail-agent.interval-test", IntervalSchedule(seconds=300), tmp_path)
    d = sched._job_to_plist_dict(job)
    assert d["StartInterval"] == 300
    assert d["RunAtLoad"] is True
    assert d["Label"] == "mail-agent.interval-test"


def test_daily_plist_has_start_calendar_interval(tmp_path):
    sched = LaunchdScheduler(agents_dir=tmp_path / "agents")
    job = _job("mail-agent.daily-test", DailySchedule(hour=9, minute=30), tmp_path)
    d = sched._job_to_plist_dict(job)
    assert d["StartCalendarInterval"] == {"Hour": 9, "Minute": 30}
    assert "StartInterval" not in d


def test_keepalive_plist(tmp_path):
    sched = LaunchdScheduler(agents_dir=tmp_path / "agents")
    job = _job("mail-agent.keep-test", KeepAliveSchedule(), tmp_path)
    d = sched._job_to_plist_dict(job)
    assert d["KeepAlive"] is True
    assert d["RunAtLoad"] is True


def test_plist_round_trips_through_plistlib(tmp_path):
    sched = LaunchdScheduler(agents_dir=tmp_path / "agents")
    job = _job("mail-agent.serializable", IntervalSchedule(seconds=600), tmp_path)
    d = sched._job_to_plist_dict(job)
    blob = plistlib.dumps(d)
    parsed = plistlib.loads(blob)
    assert parsed["Label"] == "mail-agent.serializable"


def test_installed_jobs_lists_matching_prefix(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    for name in ("mail-agent.a.plist", "mail-agent.b.plist", "other.plist"):
        (agents_dir / name).write_text("x")
    sched = LaunchdScheduler(agents_dir=agents_dir)
    assert sched.installed_jobs() == ["mail-agent.a", "mail-agent.b"]

from __future__ import annotations

from mail_agent.schedule.systemd import SystemdScheduler
from mail_agent.schedule.types import (
    DailySchedule,
    IntervalSchedule,
    JobSpec,
    KeepAliveSchedule,
)


def _job(name, schedule, tmp_path) -> JobSpec:
    return JobSpec(
        name=name,
        description="test",
        command=["/usr/bin/echo", "hello world"],
        schedule=schedule,
        working_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )


def test_service_unit_contains_exec_start(tmp_path):
    sched = SystemdScheduler(user_units_dir=tmp_path)
    job = _job("mail-agent.svc", IntervalSchedule(seconds=600), tmp_path)
    unit = sched._render_service(job, oneshot=True)
    assert "Type=oneshot" in unit
    assert "/usr/bin/echo" in unit
    assert '"hello world"' in unit  # arg with space gets quoted


def test_keepalive_service_uses_simple_and_restart(tmp_path):
    sched = SystemdScheduler(user_units_dir=tmp_path)
    job = _job("mail-agent.live", KeepAliveSchedule(), tmp_path)
    unit = sched._render_service(job, oneshot=False)
    assert "Type=simple" in unit
    assert "Restart=on-failure" in unit


def test_interval_timer_uses_on_unit_active_sec(tmp_path):
    sched = SystemdScheduler(user_units_dir=tmp_path)
    job = _job("mail-agent.poll", IntervalSchedule(seconds=1800), tmp_path)
    unit = sched._render_timer(job)
    assert "OnUnitActiveSec=1800s" in unit


def test_daily_timer_uses_on_calendar(tmp_path):
    sched = SystemdScheduler(user_units_dir=tmp_path)
    job = _job("mail-agent.daily", DailySchedule(hour=9, minute=5), tmp_path)
    unit = sched._render_timer(job)
    assert "OnCalendar=*-*-* 09:05:00" in unit

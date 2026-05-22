from __future__ import annotations

from pathlib import Path

from .types import (
    CronSchedule,
    CronWindow,
    JobSpec,
    KeepAliveSchedule,
    Weekday,
)

PREFIX = "mail-agent."


_WORKDAY_WINDOW = CronWindow(
    weekdays=frozenset({
        Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI,
    }),
    hours=frozenset({10, 12, 14, 16, 18}),
)

_WEEKEND_WINDOW = CronWindow(
    weekdays=frozenset({Weekday.SAT, Weekday.SUN}),
    hours=frozenset({10, 18}),
)

DEFAULT_TRIAGE_SCHEDULE = CronSchedule(
    windows=(_WORKDAY_WINDOW, _WEEKEND_WINDOW),
)


def default_jobs(
    project_root: Path,
    uv_bin: Path,
    include_listener: bool = True,
) -> list[JobSpec]:
    log_dir = project_root / "logs"
    common = dict(working_dir=project_root, log_dir=log_dir)

    triage_cmd = [str(uv_bin), "run", "mail-agent", "triage"]
    listener_cmd = [str(uv_bin), "run", "mail-agent", "slack", "listen"]

    jobs: list[JobSpec] = [
        JobSpec(
            name=f"{PREFIX}triage-cron",
            description="Workday-aware mail-agent triage.",
            command=triage_cmd,
            schedule=DEFAULT_TRIAGE_SCHEDULE,
            **common,
        ),
    ]
    if include_listener:
        jobs.append(
            JobSpec(
                name=f"{PREFIX}slack-listener",
                description="mail-agent Slack listener (Socket Mode, always on).",
                command=listener_cmd,
                schedule=KeepAliveSchedule(),
                **common,
            )
        )
    return jobs

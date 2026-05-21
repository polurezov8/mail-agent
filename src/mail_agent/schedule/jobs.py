from __future__ import annotations

from pathlib import Path

from .types import (
    DailySchedule,
    IntervalSchedule,
    JobSpec,
    KeepAliveSchedule,
)

PREFIX = "mail-agent."


def default_jobs(
    project_root: Path,
    uv_bin: Path,
    poll_interval_seconds: int = 1800,
    daily_hour: int = 10,
    daily_minute: int = 0,
    include_listener: bool = True,
) -> list[JobSpec]:
    log_dir = project_root / "logs"
    common = dict(working_dir=project_root, log_dir=log_dir)

    triage_cmd = [str(uv_bin), "run", "mail-agent", "triage"]
    listener_cmd = [str(uv_bin), "run", "mail-agent", "slack", "listen"]

    jobs: list[JobSpec] = [
        JobSpec(
            name=f"{PREFIX}triage-poll",
            description="Run mail-agent triage on a fixed interval.",
            command=triage_cmd,
            schedule=IntervalSchedule(seconds=poll_interval_seconds),
            **common,
        ),
        JobSpec(
            name=f"{PREFIX}triage-daily",
            description="Daily mail-agent triage sweep (catch-all).",
            command=triage_cmd,
            schedule=DailySchedule(hour=daily_hour, minute=daily_minute),
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

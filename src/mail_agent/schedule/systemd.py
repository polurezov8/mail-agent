from __future__ import annotations

import subprocess
from pathlib import Path

from .types import (
    CronSchedule,
    DailySchedule,
    IntervalSchedule,
    JobSpec,
    JobStatus,
    KeepAliveSchedule,
    Weekday,
)


def _quote(s: str) -> str:
    if " " in s or '"' in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


_DAY_SHORT = {
    Weekday.SUN: "Sun",
    Weekday.MON: "Mon",
    Weekday.TUE: "Tue",
    Weekday.WED: "Wed",
    Weekday.THU: "Thu",
    Weekday.FRI: "Fri",
    Weekday.SAT: "Sat",
}


def _cron_to_oncalendar_lines(s: CronSchedule) -> list[str]:
    lines: list[str] = []
    for w in s.windows:
        # SUN=0 in Weekday (launchd numbering), but calendar convention prints it last
        days = ",".join(
            _DAY_SHORT[d]
            for d in sorted(w.weekdays, key=lambda d: 7 if d == Weekday.SUN else int(d))
        )
        hours = ",".join(f"{h:02d}" for h in sorted(w.hours))
        lines.append(f"OnCalendar={days} {hours}:{w.minute:02d}:00")
    return lines


class SystemdScheduler:
    """Linux user-level systemd units. No root needed — uses `systemctl --user`."""

    name = "systemd"

    def __init__(self, user_units_dir: Path | None = None) -> None:
        self.user_units_dir = user_units_dir or Path.home() / ".config" / "systemd" / "user"

    def _render_service(self, job: JobSpec, oneshot: bool) -> str:
        job.log_dir.mkdir(parents=True, exist_ok=True)
        exec_start = " ".join(_quote(arg) for arg in job.command)
        type_line = "Type=oneshot" if oneshot else "Type=simple"
        restart_block = "" if oneshot else "Restart=on-failure\nRestartSec=10\n"
        env_lines = "".join(f"Environment={k}={v}\n" for k, v in job.env.items())
        return (
            f"[Unit]\n"
            f"Description={job.description}\n\n"
            f"[Service]\n"
            f"{type_line}\n"
            f"WorkingDirectory={job.working_dir}\n"
            f"ExecStart={exec_start}\n"
            f"{env_lines}"
            f"{restart_block}"
            f"StandardOutput=append:{job.log_dir}/{job.name}.out.log\n"
            f"StandardError=append:{job.log_dir}/{job.name}.err.log\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )

    def _render_timer(self, job: JobSpec) -> str:
        s = job.schedule
        if isinstance(s, IntervalSchedule):
            on_line = f"OnUnitActiveSec={s.seconds}s\nOnBootSec=60s"
        elif isinstance(s, DailySchedule):
            on_line = f"OnCalendar=*-*-* {s.hour:02d}:{s.minute:02d}:00"
        elif isinstance(s, CronSchedule):
            on_line = "\n".join(_cron_to_oncalendar_lines(s))
        else:
            raise ValueError(f"Cannot timer-schedule {s.kind}")
        return (
            f"[Unit]\n"
            f"Description={job.description} (timer)\n\n"
            f"[Timer]\n"
            f"{on_line}\n"
            f"Persistent=true\n\n"
            f"[Install]\n"
            f"WantedBy=timers.target\n"
        )

    def install(self, jobs: list[JobSpec]) -> None:
        self.user_units_dir.mkdir(parents=True, exist_ok=True)
        for job in jobs:
            service_path = self.user_units_dir / f"{job.name}.service"
            if isinstance(job.schedule, KeepAliveSchedule):
                service_path.write_text(self._render_service(job, oneshot=False))
            else:
                service_path.write_text(self._render_service(job, oneshot=True))
                (self.user_units_dir / f"{job.name}.timer").write_text(self._render_timer(job))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        for job in jobs:
            unit = (
                f"{job.name}.service"
                if isinstance(job.schedule, KeepAliveSchedule)
                else f"{job.name}.timer"
            )
            subprocess.run(["systemctl", "--user", "enable", "--now", unit], check=True)

    def uninstall(self, name_prefix: str = "mail-agent.") -> list[str]:
        removed: list[str] = []
        for unit in list(self.user_units_dir.glob(f"{name_prefix}*.timer")) + list(
            self.user_units_dir.glob(f"{name_prefix}*.service")
        ):
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", unit.name],
                check=False,
                capture_output=True,
            )
            unit.unlink()
            if unit.stem not in removed:
                removed.append(unit.stem)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        return removed

    def installed_jobs(self, name_prefix: str = "mail-agent.") -> list[str]:
        names: set[str] = set()
        for unit in self.user_units_dir.glob(f"{name_prefix}*"):
            names.add(unit.stem)
        return sorted(names)

    def restart(self, name_prefix: str = "mail-agent.", only: str | None = None) -> list[str]:
        """`systemctl --user restart` each matching service. Returns names restarted."""
        restarted: list[str] = []
        targets = [only] if only else self.installed_jobs(name_prefix)
        for name in targets:
            result = subprocess.run(
                ["systemctl", "--user", "restart", f"{name}.service"],
                capture_output=True,
            )
            if result.returncode == 0:
                restarted.append(name)
        return restarted

    def status(self, name_prefix: str = "mail-agent.") -> list[JobStatus]:
        """Query timer for interval/daily jobs, service for keep-alive jobs.

        Without this dispatch, interval jobs (which only have an enabled timer
        between fires) always report as inactive — `doctor` would warn even
        though everything is healthy.
        """
        results: list[JobStatus] = []
        for name in self.installed_jobs(name_prefix):
            timer_unit = f"{name}.timer"
            service_unit = f"{name}.service"
            unit = timer_unit if (self.user_units_dir / timer_unit).exists() else service_unit
            active = subprocess.run(
                ["systemctl", "--user", "is-active", unit],
                capture_output=True,
                text=True,
            ).stdout.strip()
            enabled = subprocess.run(
                ["systemctl", "--user", "is-enabled", unit],
                capture_output=True,
                text=True,
            ).stdout.strip()
            results.append(
                JobStatus(
                    name=name,
                    installed=True,
                    enabled=(enabled in ("enabled", "static")),
                    running=(active == "active"),
                )
            )
        return results

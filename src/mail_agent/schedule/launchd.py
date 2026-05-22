from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from .types import (
    CronSchedule,
    DailySchedule,
    IntervalSchedule,
    JobSpec,
    JobStatus,
    KeepAliveSchedule,
)


def _cron_to_plist_entries(s: CronSchedule) -> list[dict]:
    entries: list[dict] = []
    for w in s.windows:
        for day in sorted(w.weekdays):
            for hour in sorted(w.hours):
                entries.append({
                    "Weekday": int(day),
                    "Hour": hour,
                    "Minute": w.minute,
                })
    return entries


class LaunchdScheduler:
    name = "launchd"

    def __init__(self, agents_dir: Path | None = None) -> None:
        self.agents_dir = agents_dir or Path.home() / "Library" / "LaunchAgents"

    def _plist_path(self, job_name: str) -> Path:
        return self.agents_dir / f"{job_name}.plist"

    def _job_to_plist_dict(self, job: JobSpec) -> dict:
        job.log_dir.mkdir(parents=True, exist_ok=True)
        d: dict = {
            "Label": job.name,
            "ProgramArguments": job.command,
            "WorkingDirectory": str(job.working_dir),
            "StandardOutPath": str(job.log_dir / f"{job.name}.out.log"),
            "StandardErrorPath": str(job.log_dir / f"{job.name}.err.log"),
            "EnvironmentVariables": {
                "PATH": (
                    f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{Path.home()}/.local/bin"
                ),
                **job.env,
            },
        }
        s = job.schedule
        if isinstance(s, IntervalSchedule):
            d["StartInterval"] = s.seconds
            d["RunAtLoad"] = True
        elif isinstance(s, DailySchedule):
            d["StartCalendarInterval"] = {"Hour": s.hour, "Minute": s.minute}
        elif isinstance(s, CronSchedule):
            d["StartCalendarInterval"] = _cron_to_plist_entries(s)
        elif isinstance(s, KeepAliveSchedule):
            d["KeepAlive"] = True
            d["RunAtLoad"] = True
        return d

    def install(self, jobs: list[JobSpec]) -> None:
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        for job in jobs:
            plist_path = self._plist_path(job.name)
            with open(plist_path, "wb") as f:
                plistlib.dump(self._job_to_plist_dict(job), f)
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                check=False,
                capture_output=True,
            )
            subprocess.run(["launchctl", "load", str(plist_path)], check=True)

    def uninstall(self, name_prefix: str = "mail-agent.") -> list[str]:
        removed: list[str] = []
        for plist in self.agents_dir.glob(f"{name_prefix}*.plist"):
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                check=False,
                capture_output=True,
            )
            plist.unlink()
            removed.append(plist.stem)
        return removed

    def installed_jobs(self, name_prefix: str = "mail-agent.") -> list[str]:
        return sorted(p.stem for p in self.agents_dir.glob(f"{name_prefix}*.plist"))

    def restart(self, name_prefix: str = "mail-agent.", only: str | None = None) -> list[str]:
        """`launchctl kickstart -k` each matching job. Returns names restarted."""
        import os

        uid = os.getuid()
        restarted: list[str] = []
        targets = [only] if only else self.installed_jobs(name_prefix)
        for name in targets:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{name}"],
                capture_output=True,
            )
            if result.returncode == 0:
                restarted.append(name)
        return restarted

    def status(self, name_prefix: str = "mail-agent.") -> list[JobStatus]:
        listing: dict[str, tuple[str, str]] = {}
        try:
            out = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, check=True
            ).stdout
            for line in out.splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                pid, status, label = parts[0].strip(), parts[1].strip(), parts[2].strip()
                listing[label] = (pid, status)
        except subprocess.CalledProcessError:
            pass

        results: list[JobStatus] = []
        for name in self.installed_jobs(name_prefix):
            entry = listing.get(name)
            if entry is None:
                results.append(JobStatus(name=name, installed=True, enabled=False, running=False))
                continue
            pid, status = entry
            running = pid not in ("-", "")
            try:
                last_exit = int(status) if status not in ("-", "") else None
            except ValueError:
                last_exit = None
            results.append(
                JobStatus(
                    name=name,
                    installed=True,
                    enabled=True,
                    running=running,
                    last_exit_code=last_exit,
                )
            )
        return results

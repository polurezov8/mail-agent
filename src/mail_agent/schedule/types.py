from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, Field, field_validator


class Weekday(IntEnum):
    """Day-of-week values aligned with launchd `StartCalendarInterval` (0=Sun..6=Sat)."""

    SUN = 0
    MON = 1
    TUE = 2
    WED = 3
    THU = 4
    FRI = 5
    SAT = 6


class CronWindow(BaseModel):
    """One firing window. Empty weekdays or hours = illegal at construction."""

    weekdays: frozenset[Weekday] = Field(min_length=1)
    hours: frozenset[int] = Field(min_length=1)
    minute: int = Field(default=0, ge=0, le=59)

    @field_validator("hours")
    @classmethod
    def _hours_in_range(cls, v: frozenset[int]) -> frozenset[int]:
        if not all(0 <= h <= 23 for h in v):
            raise ValueError("hours must be 0..23")
        return v


class CronSchedule(BaseModel):
    """Multi-window calendar schedule. Windows are OR'd at the backend layer."""

    kind: Literal["cron"] = "cron"
    windows: tuple[CronWindow, ...] = Field(min_length=1)


class IntervalSchedule(BaseModel):
    """Fire every N seconds. RunAtLoad-equivalent on Mac, OnBootSec on Linux."""

    kind: Literal["interval"] = "interval"
    seconds: int = Field(gt=0)


class DailySchedule(BaseModel):
    """Fire once a day at local HH:MM."""

    kind: Literal["daily"] = "daily"
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class KeepAliveSchedule(BaseModel):
    """Always running. Restart on crash. For long-lived listeners."""

    kind: Literal["keep_alive"] = "keep_alive"


Schedule: TypeAlias = (
    IntervalSchedule | DailySchedule | KeepAliveSchedule | CronSchedule
)


class JobSpec(BaseModel):
    """Platform-agnostic job description. Schedulers translate to plist/unit files."""

    name: str  # full label, e.g. "mail-agent.triage-cron"
    description: str
    command: list[str]
    schedule: Schedule
    working_dir: Path
    log_dir: Path
    env: dict[str, str] = Field(default_factory=dict)


class JobStatus(BaseModel):
    name: str
    installed: bool
    enabled: bool
    running: bool
    last_exit_code: int | None = None

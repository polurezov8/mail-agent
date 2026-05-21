from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, Field


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


Schedule: TypeAlias = IntervalSchedule | DailySchedule | KeepAliveSchedule


class JobSpec(BaseModel):
    """Platform-agnostic job description. Schedulers translate to plist/unit files."""

    name: str  # full label, e.g. "mail-agent.triage-poll"
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

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .base import Scheduler
from .launchd import LaunchdScheduler
from .systemd import SystemdScheduler


def get_scheduler() -> Scheduler:
    if sys.platform == "darwin":
        return LaunchdScheduler()
    if sys.platform.startswith("linux"):
        return SystemdScheduler()
    raise RuntimeError(
        f"Unsupported platform: {sys.platform}. "
        "Add a new Scheduler implementation in mail_agent.schedule."
    )


def detect_uv() -> Path:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "`uv` not found in PATH. Install: https://docs.astral.sh/uv/getting-started/installation/"
        )
    return Path(uv).resolve()


def detect_project_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        if (p / "pyproject.toml").exists() and (p / "src" / "mail_agent").exists():
            return p
    raise RuntimeError(
        "Could not locate mail-agent project root (looking for pyproject.toml + src/mail_agent/). "
        "Run `mail-agent schedule install` from the repo directory."
    )

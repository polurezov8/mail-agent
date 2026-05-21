from __future__ import annotations

from typing import Protocol

from .types import JobSpec, JobStatus


class Scheduler(Protocol):
    """Platform-agnostic scheduler contract.

    Implementations: LaunchdScheduler (macOS), SystemdScheduler (Linux user units).
    Adding a new platform = one new file implementing this protocol.
    """

    name: str

    def install(self, jobs: list[JobSpec]) -> None: ...

    def uninstall(self, name_prefix: str = "mail-agent.") -> list[str]: ...

    def status(self, name_prefix: str = "mail-agent.") -> list[JobStatus]: ...

    def installed_jobs(self, name_prefix: str = "mail-agent.") -> list[str]: ...

    def restart(self, name_prefix: str = "mail-agent.", only: str | None = None) -> list[str]: ...

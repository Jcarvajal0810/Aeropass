"""Health check port (spec 002, data-model.md §5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HealthCheck(Protocol):
    name: str

    async def check(self) -> None:
        """Return if the dependency answers; raise otherwise."""
        ...


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    # Names of the failed checks: for internal logs only, never in the HTTP response.
    fallidos: tuple[str, ...] = ()

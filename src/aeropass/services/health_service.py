"""Service availability for the Sentry uptime monitor (spec 002, FR-013a, research §9)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from aeropass.ports.health import HealthCheck, HealthReport

logger = logging.getLogger("aeropass.health")

CHECK_TIMEOUT_SECONDS = 3.0


class HealthService:
    def __init__(
        self, checks: Sequence[HealthCheck], timeout: float = CHECK_TIMEOUT_SECONDS
    ) -> None:
        self._checks = tuple(checks)
        self._timeout = timeout

    async def check(self) -> HealthReport:
        """Runs every check in parallel, each bounded by the timeout."""
        passed = await asyncio.gather(*(self._passes(check) for check in self._checks))
        fallidos = tuple(c.name for c, ok in zip(self._checks, passed, strict=True) if not ok)
        if fallidos:
            logger.warning("health check failed: %s", ", ".join(fallidos))
        return HealthReport(ok=not fallidos, fallidos=fallidos)

    async def _passes(self, check: HealthCheck) -> bool:
        try:
            await asyncio.wait_for(check.check(), timeout=self._timeout)
        except Exception:  # any failure or timeout means "unavailable"; details stay internal
            return False
        return True

"""HealthService (spec 002, research §9). Written before the service."""

from __future__ import annotations

import asyncio
import logging
import time

from aeropass.services.health_service import HealthService


class Check:
    def __init__(self, name: str, *, delay: float = 0.0, fails: bool = False) -> None:
        self.name = name
        self._delay = delay
        self._fails = fails

    async def check(self) -> None:
        await asyncio.sleep(self._delay)
        if self._fails:
            raise ConnectionError("down")


async def test_checks_run_in_parallel():
    service = HealthService([Check("database", delay=0.2), Check("redis", delay=0.2)], timeout=1)

    started = time.perf_counter()
    report = await service.check()

    assert report.ok
    assert time.perf_counter() - started < 0.35


async def test_the_timeout_bounds_the_total_time():
    service = HealthService([Check("database", delay=5)], timeout=0.1)

    started = time.perf_counter()
    report = await service.check()

    assert not report.ok
    assert time.perf_counter() - started < 0.5


async def test_the_report_lists_the_failed_checks_and_logs_them(caplog):
    caplog.set_level(logging.WARNING, logger="aeropass.health")
    service = HealthService([Check("database"), Check("redis", fails=True)], timeout=1)

    report = await service.check()

    assert not report.ok
    assert report.fallidos == ("redis",)
    assert [r.name for r in caplog.records] == ["aeropass.health"]
    assert "redis" in caplog.records[0].getMessage()


async def test_without_checks_it_is_ok():
    assert (await HealthService([]).check()).ok

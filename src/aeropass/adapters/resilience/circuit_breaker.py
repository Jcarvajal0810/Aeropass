"""Circuit breaker with pluggable, shareable state (constitution Principle V, research R5).

In serverless, in-process breaker state is lost on every cold start, so the state lives behind
``BreakerStateStore`` (Upstash Redis in production, in-memory in tests).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol, TypeVar

from aeropass.observability.hooks import emit_audit, span
from aeropass.observability.telemetry_catalog import CIRCUIT_OPENED
from aeropass.ports.clock import Clock

logger = logging.getLogger(__name__)

T = TypeVar("T")

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"circuit '{name}' is open")
        self.name = name


@dataclass(frozen=True)
class BreakerState:
    status: str = CLOSED
    failures: int = 0
    window_start: float = 0.0
    open_until: float = 0.0
    trial_until: float = 0.0

    def to_mapping(self) -> dict[str, str]:
        return {
            "status": self.status,
            "failures": str(self.failures),
            "window_start": repr(self.window_start),
            "open_until": repr(self.open_until),
            "trial_until": repr(self.trial_until),
        }

    @classmethod
    def from_mapping(cls, data: dict[str, str] | None) -> BreakerState:
        if not data:
            return cls()
        return cls(
            status=data.get("status", CLOSED),
            failures=int(data.get("failures", 0)),
            window_start=float(data.get("window_start", 0)),
            open_until=float(data.get("open_until", 0)),
            trial_until=float(data.get("trial_until", 0)),
        )


class BreakerStateStore(Protocol):
    async def get(self, name: str) -> BreakerState: ...

    async def save(self, name: str, state: BreakerState) -> None: ...


class InMemoryBreakerStateStore:
    def __init__(self) -> None:
        self._states: dict[str, BreakerState] = {}

    async def get(self, name: str) -> BreakerState:
        return self._states.get(name, BreakerState())

    async def save(self, name: str, state: BreakerState) -> None:
        self._states[name] = state


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        store: BreakerStateStore,
        clock: Clock,
        *,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        open_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self._store = store
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._window_seconds = window_seconds
        self._open_seconds = open_seconds

    async def state(self) -> BreakerState:
        try:
            return await self._store.get(self.name)
        except Exception:  # a broken state store must not take the caller down
            logger.warning("circuit breaker state unavailable", extra={"breaker": self.name})
            return BreakerState()

    async def _save(self, state: BreakerState) -> None:
        try:
            await self._store.save(self.name, state)
        except Exception:
            logger.warning("circuit breaker state not saved", extra={"breaker": self.name})

    # The explicit per-call timeout is part of the breaker contract (constitution, Principle V).
    async def call(self, fn: Callable[[], Awaitable[T]], *, timeout: float) -> T:  # noqa: ASYNC109
        # One span per call, rejections included: its status tells timeout, open circuit and
        # code error apart (spec 002, research §7).
        with span(f"circuit_breaker.{self.name}"):
            return await self._call(fn, timeout)

    async def _call(self, fn: Callable[[], Awaitable[T]], timeout: float) -> T:  # noqa: ASYNC109
        now = self._clock.now().timestamp()
        state = await self.state()

        if state.status == OPEN:
            if now < state.open_until:
                raise CircuitOpenError(self.name)
            state = replace(state, status=HALF_OPEN, trial_until=0.0)

        if state.status == HALF_OPEN:
            if now < state.trial_until:
                raise CircuitOpenError(self.name)  # another trial call is in flight
            state = replace(state, trial_until=now + timeout + 1)
            await self._save(state)

        try:
            result = await asyncio.wait_for(fn(), timeout=timeout)
        except Exception:
            await self._record_failure(state, self._clock.now().timestamp())
            raise

        if state.status != CLOSED or state.failures:
            await self._save(BreakerState())
        return result

    async def _record_failure(self, state: BreakerState, now: float) -> None:
        if state.status == HALF_OPEN:
            await self._save(BreakerState(status=OPEN, open_until=now + self._open_seconds))
            self._opened()
            return
        if now - state.window_start > self._window_seconds:
            state = BreakerState(failures=1, window_start=now)
        else:
            state = replace(state, failures=state.failures + 1)
        opened = state.failures >= self._failure_threshold
        if opened:
            state = BreakerState(status=OPEN, open_until=now + self._open_seconds)
            logger.warning("circuit breaker opened", extra={"breaker": self.name})
        await self._save(state)
        if opened:
            self._opened()

    def _opened(self) -> None:
        """Contingency signal per dependency (spec 002, FR-008, FR-014)."""
        emit_audit(CIRCUIT_OPENED, dependencia=self.name)

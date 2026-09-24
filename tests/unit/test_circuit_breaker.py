import asyncio
import contextlib

import pytest

from aeropass.adapters.clock import FakeClock
from aeropass.adapters.resilience.circuit_breaker import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    CircuitBreaker,
    CircuitOpenError,
    InMemoryBreakerStateStore,
)
from aeropass.observability import hooks
from aeropass.observability import telemetry_catalog as tc


class Boom(Exception):
    pass


async def fail() -> None:
    raise Boom()


async def ok() -> str:
    return "ok"


@pytest.fixture
def breaker() -> tuple[CircuitBreaker, FakeClock, InMemoryBreakerStateStore]:
    clock = FakeClock()
    store = InMemoryBreakerStateStore()
    return CircuitBreaker("test", store, clock), clock, store


async def _fail_n(cb: CircuitBreaker, n: int) -> None:
    for _ in range(n):
        with pytest.raises(Boom):
            await cb.call(fail, timeout=1)


async def test_opens_after_five_failures_within_window(breaker):
    cb, _, _ = breaker
    await _fail_n(cb, 4)
    assert (await cb.state()).status == CLOSED
    await _fail_n(cb, 1)
    assert (await cb.state()).status == OPEN


async def test_failures_outside_window_do_not_accumulate(breaker):
    cb, clock, _ = breaker
    await _fail_n(cb, 4)
    clock.advance(61)
    await _fail_n(cb, 4)
    assert (await cb.state()).status == CLOSED


async def test_fails_fast_while_open(breaker):
    cb, _, _ = breaker
    await _fail_n(cb, 5)
    called = False

    async def spy() -> None:
        nonlocal called
        called = True

    with pytest.raises(CircuitOpenError):
        await cb.call(spy, timeout=1)
    assert not called


async def test_half_open_after_open_period_allows_one_call_and_closes_on_success(breaker):
    cb, clock, _ = breaker
    await _fail_n(cb, 5)
    clock.advance(31)
    assert await cb.call(ok, timeout=1) == "ok"
    assert (await cb.state()).status == CLOSED


async def test_half_open_failure_reopens(breaker):
    cb, clock, _ = breaker
    await _fail_n(cb, 5)
    clock.advance(31)
    await _fail_n(cb, 1)
    state = await cb.state()
    assert state.status == OPEN
    assert state.open_until == pytest.approx(clock.now().timestamp() + 30)


async def test_half_open_blocks_concurrent_trial(breaker):
    cb, clock, store = breaker
    await _fail_n(cb, 5)
    clock.advance(31)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> str:
        started.set()
        await release.wait()
        return "ok"

    trial = asyncio.create_task(cb.call(slow, timeout=5))
    await started.wait()
    assert (await store.get("test")).status == HALF_OPEN
    with pytest.raises(CircuitOpenError):
        await cb.call(ok, timeout=1)
    release.set()
    assert await trial == "ok"


# --- observability (spec 002, research §7, FR-008, FR-014) ----------------------------------
@pytest.fixture
def telemetry():
    audits: list[tuple[str, dict]] = []
    spans: list[str] = []

    @contextlib.contextmanager
    def span_hook(name: str):
        spans.append(name)
        yield

    hooks.clear_hooks()
    hooks.register_audit_hook(lambda event, data: audits.append((event, data)))
    hooks.register_span_hook(span_hook)
    yield audits, spans
    hooks.clear_hooks()


def _openings(audits: list[tuple[str, dict]]) -> list[dict]:
    return [data for event, data in audits if event == tc.CIRCUIT_OPENED]


async def test_opening_is_audited_with_its_dependency(breaker, telemetry):
    cb, _, _ = breaker
    audits, _ = telemetry

    await _fail_n(cb, 4)
    assert _openings(audits) == []  # a failure that does not open emits nothing

    await _fail_n(cb, 1)
    assert _openings(audits) == [{"dependencia": "test"}]


async def test_reopening_from_half_open_is_audited_too(breaker, telemetry):
    cb, clock, _ = breaker
    audits, _ = telemetry
    await _fail_n(cb, 5)
    clock.advance(31)

    await _fail_n(cb, 1)

    assert _openings(audits) == [{"dependencia": "test"}, {"dependencia": "test"}]


async def test_every_call_runs_inside_its_span_even_when_rejected(breaker, telemetry):
    cb, _, _ = breaker
    _, spans = telemetry

    await cb.call(ok, timeout=1)
    await _fail_n(cb, 5)
    with pytest.raises(CircuitOpenError):
        await cb.call(ok, timeout=1)

    assert spans == ["circuit_breaker.test"] * 7


async def test_timeouts_count_as_failures(breaker):
    cb, _, _ = breaker

    async def hang() -> None:
        await asyncio.sleep(10)

    for _ in range(5):
        with pytest.raises(TimeoutError):
            await cb.call(hang, timeout=0.01)
    assert (await cb.state()).status == OPEN

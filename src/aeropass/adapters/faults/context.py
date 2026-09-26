"""Per-request fault plan (spec 003).

The middleware parses ``X-AeroPass-Fault`` into a ``FaultPlan`` and activates it for the request
in a ``ContextVar``: async-safe, scoped to the request, no global state. The fault wrappers ask
``trip(name)`` whether to fail; the first trip of each fault in a request emits the
``fault.injected`` audit record (log, metric-free, and the Sentry ``fault_injected`` tag).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from aeropass.observability.hooks import emit_audit
from aeropass.observability.telemetry_catalog import FAULT_INJECTED

logger = logging.getLogger("aeropass.faults")

BLOB_DOWN = "blob_down"
MXFACE_DOWN = "mxface_down"
MXFACE_SLOW = "mxface_slow"
MXFACE_QUOTA = "mxface_quota"
DB_DOWN = "db_down"
REDIS_DOWN = "redis_down"
SIGNING_DOWN = "signing_down"
QSTASH_DOWN = "qstash_down"
# The whole request is delayed (a visible ``fault.slow`` step): moves W6 and W9.
SLOW = "slow"

KNOWN_FAULTS = frozenset(
    {
        BLOB_DOWN,
        MXFACE_DOWN,
        MXFACE_SLOW,
        MXFACE_QUOTA,
        DB_DOWN,
        REDIS_DOWN,
        SIGNING_DOWN,
        QSTASH_DOWN,
        SLOW,
    }
)

# ``mxface_slow`` without a value, and the cap on any value (Vercel cuts functions at 30 s).
DEFAULT_SLOW_MS = 12_000
MAX_SLOW_MS = 60_000
# ``slow`` without a value: enough to break the p95 target of KR A2.4 (3 s).
DEFAULT_REQUEST_SLOW_MS = 4_000


@dataclass(frozen=True)
class FaultPlan:
    faults: frozenset[str] = frozenset()
    slow_ms: int = DEFAULT_SLOW_MS
    request_slow_ms: int = DEFAULT_REQUEST_SLOW_MS
    # Faults that actually fired in this request (shared by reference with the middleware).
    applied: set[str] = field(default_factory=set, compare=False, hash=False)


_plan: ContextVar[FaultPlan | None] = ContextVar("aeropass_fault_plan", default=None)


def parse(header: str) -> tuple[FaultPlan, list[str]]:
    """``"blob_down,mxface_slow:8000"`` -> plan, plus the names nobody knows."""
    faults: set[str] = set()
    unknown: list[str] = []
    slow_ms = DEFAULT_SLOW_MS
    request_slow_ms = DEFAULT_REQUEST_SLOW_MS
    for raw in header.split(","):
        item = raw.strip().lower()
        if not item:
            continue
        name, _, value = item.partition(":")
        if name not in KNOWN_FAULTS:
            unknown.append(item)
            continue
        if name in (MXFACE_SLOW, SLOW) and value:
            try:
                ms = max(0, min(int(value), MAX_SLOW_MS))
            except ValueError:
                unknown.append(item)
                continue
            if name == MXFACE_SLOW:
                slow_ms = ms
            else:
                request_slow_ms = ms
        faults.add(name)
    return FaultPlan(frozenset(faults), slow_ms, request_slow_ms), unknown


def activate(plan: FaultPlan) -> Token[FaultPlan | None]:
    return _plan.set(plan)


def deactivate(token: Token[FaultPlan | None]) -> None:
    _plan.reset(token)


def current() -> FaultPlan | None:
    return _plan.get()


def trip(name: str) -> bool:
    """True when this request asked for ``name``: the caller must then fail as the real
    dependency would. Records the fault the first time it fires in the request."""
    plan = _plan.get()
    if plan is None or name not in plan.faults:
        return False
    if name not in plan.applied:
        plan.applied.add(name)
        logger.warning("fault injected: %s", name)
        emit_audit(FAULT_INJECTED, fault=name)
    return True

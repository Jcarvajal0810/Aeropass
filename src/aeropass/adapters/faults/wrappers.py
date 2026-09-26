"""Fault wrappers (spec 003, FR-005/FR-006).

Each wrapper stands in front of one dependency and, when the request asked for its fault, fails
with **the same exception the real dependency raises** there. The services and handlers then
answer as they would in a real incident: no special branches in the business code. With the
fault not asked for, every wrapper delegates unchanged.

Where the real adapter handles the failure itself (Redis: the rate limiter fails open and the
token store raises ``TokenStoreUnavailable``; Blob: ``MediaUnavailable``), the fault is injected
underneath the adapter, in the client, so that handling runs as in production.

The composition root (``api/deps.py``) builds these only with ``FAULT_INJECTION_ENABLED``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import OperationalError

from aeropass.adapters.faults.context import (
    DB_DOWN,
    MXFACE_DOWN,
    MXFACE_QUOTA,
    MXFACE_SLOW,
    QSTASH_DOWN,
    REDIS_DOWN,
    SIGNING_DOWN,
    current,
    trip,
)
from aeropass.domain.images import ImageInput
from aeropass.domain.verification import BiometricResult
from aeropass.ports.biometric_provider import BiometricProvider, ProviderUnavailable
from aeropass.ports.event_publisher import EventPublisher, PublishFailed
from aeropass.ports.rate_limiter import RateLimiter, RateLimitResult


class FaultProxy:
    """Delegates every attribute to ``inner``; a method call fails with ``error()`` while the
    request asks for ``fault``. For clients and adapters whose methods are all I/O (the Upstash
    Redis client, the Vercel Blob client, the in-memory doubles in fake mode)."""

    def __init__(self, inner: Any, fault: str, error: Callable[[], Exception]) -> None:
        self._inner = inner
        self._fault = fault
        self._error = error

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            if trip(self._fault):
                raise self._error()
            return attribute(*args, **kwargs)

        return call


class FaultInjectingBiometricProvider:
    """Wraps the provider **inside** ``ResilientBiometricProvider``, so the circuit breaker and
    its timeout react for real (5 failures in 60 s open it)."""

    def __init__(self, inner: BiometricProvider) -> None:
        self._inner = inner
        self.name = inner.name

    async def evaluate(self, selfie: ImageInput, referencia: ImageInput) -> BiometricResult:
        if trip(MXFACE_DOWN):
            raise ProviderUnavailable("fault injected: provider down")
        if trip(MXFACE_QUOTA):
            raise ProviderUnavailable("provider status 429")
        if trip(MXFACE_SLOW):
            plan = current()
            await asyncio.sleep((plan.slow_ms if plan else 0) / 1000)
        return await self._inner.evaluate(selfie, referencia)


class FaultInjectingEventPublisher:
    """Wraps the publisher inside ``ResilientEventPublisher`` (breaker ``qstash``)."""

    def __init__(self, inner: EventPublisher) -> None:
        self._inner = inner

    async def publish(self, event_id: str, tipo: str, payload: dict[str, Any]) -> None:
        if trip(QSTASH_DOWN):
            raise PublishFailed("fault injected: qstash down")
        await self._inner.publish(event_id, tipo, payload)


class FaultInjectingRateLimiter:
    """Fake mode only: the in-memory limiter has no Redis underneath, so this reproduces the
    real adapter's documented behaviour with Redis down: fail open (F-02)."""

    def __init__(self, inner: RateLimiter) -> None:
        self._inner = inner

    async def hit(self, key: str) -> RateLimitResult:
        if trip(REDIS_DOWN):
            return RateLimitResult(allowed=True)
        return await self._inner.hit(key)


class FaultInjectingSessionFactory:
    """Opening a database session fails as a lost connection does (``OperationalError``)."""

    def __init__(self, inner: Callable[[], Any]) -> None:
        self._inner = inner

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if trip(DB_DOWN):
            raise OperationalError(
                "fault injected: db_down", None, ConnectionError("database unavailable")
            )
        return self._inner(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SigningUnavailable(RuntimeError):
    """The pass signing key could not be used (spec 003, ``signing_down``)."""


class FaultInjectingSigner:
    """``sign`` fails; everything else (kid, public key, JWKS) is the real signer's."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def sign(self, claims: dict[str, Any]) -> str:
        if trip(SIGNING_DOWN):
            raise SigningUnavailable("fault injected: signing down")
        return str(self._inner.sign(claims))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def redis_error() -> Exception:
    return ConnectionError("fault injected: redis_down")

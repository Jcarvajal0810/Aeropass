"""Event publishing through QStash URL groups (Observer: producers never know the consumers)."""

from __future__ import annotations

from typing import Any

from qstash import AsyncQStash

from aeropass.adapters.resilience.circuit_breaker import CircuitBreaker, CircuitOpenError
from aeropass.observability.hooks import traced
from aeropass.ports.event_publisher import EventPublisher, PublishFailed


class QStashEventPublisher:
    def __init__(self, client: AsyncQStash, url_group: str) -> None:
        self._client = client
        self._url_group = url_group

    @traced("qstash.publish")
    async def publish(self, event_id: str, tipo: str, payload: dict[str, Any]) -> None:
        try:
            await self._client.message.publish_json(
                url_group=self._url_group,
                body=payload,
                deduplication_id=event_id,
                headers={"Aeropass-Event-Type": tipo},
                label=tipo,
            )
        except Exception as exc:  # the SDK raises several error types; all mean "not handed over"
            raise PublishFailed(type(exc).__name__) from exc


class ResilientEventPublisher:
    """Wraps any publisher with the ``qstash`` circuit breaker and an explicit timeout."""

    def __init__(self, inner: EventPublisher, breaker: CircuitBreaker, timeout: float) -> None:
        self.inner = inner
        self._breaker = breaker
        self._timeout = timeout

    async def publish(self, event_id: str, tipo: str, payload: dict[str, Any]) -> None:
        try:
            await self._breaker.call(
                lambda: self.inner.publish(event_id, tipo, payload), timeout=self._timeout
            )
        except (CircuitOpenError, TimeoutError) as exc:
            raise PublishFailed(type(exc).__name__) from exc

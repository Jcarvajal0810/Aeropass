"""Transactional-outbox dispatcher (research R7).

Claim → publish → record, each step in its own short transaction, so no DB connection is held
while QStash is called. A lease on ``proximo_intento_at`` keeps concurrent dispatchers (the
post-commit attempt and the scheduled sweep) from double-sending; QStash's deduplication id is
the final guard.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from aeropass.domain.events import OutboxEntry
from aeropass.observability.hooks import traced
from aeropass.ports.clock import Clock
from aeropass.ports.event_publisher import EventPublisher, PublishFailed
from aeropass.ports.repositories import UnitOfWork

logger = logging.getLogger(__name__)

LEASE_SECONDS = 30


@dataclass(frozen=True)
class DispatchReport:
    entregados: int
    pendientes: int


class OutboxDispatcher:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], publisher: EventPublisher, clock: Clock
    ) -> None:
        self._uow = uow_factory
        self._publisher = publisher
        self._clock = clock

    @traced("outbox.publish_now")
    async def publish_now(self, event_ids: Sequence[uuid.UUID]) -> None:
        """Best effort right after commit. Never raises: the scheduled sweep will retry."""
        if not event_ids:
            return
        try:
            await self._dispatch(limit=len(event_ids), ids=event_ids)
        except Exception:
            logger.exception("post-commit publish failed; left for the scheduled dispatcher")

    @traced("outbox.dispatch_due")
    async def dispatch_due(self, limit: int = 100) -> DispatchReport:
        entregados = await self._dispatch(limit=limit, ids=None)
        async with self._uow() as uow:
            pendientes = await uow.outbox.count_pending()
        return DispatchReport(entregados=entregados, pendientes=pendientes)

    async def _dispatch(self, *, limit: int, ids: Sequence[uuid.UUID] | None) -> int:
        now = self._clock.now()
        async with self._uow() as uow:
            claimed = await uow.outbox.claim_due(
                now, now + timedelta(seconds=LEASE_SECONDS), limit, ids
            )
            await uow.commit()

        entregados = 0
        for entry in claimed:
            outcome = await self._publish(entry)
            async with self._uow() as uow:
                await uow.outbox.save(outcome)
                await uow.commit()
            entregados += outcome.entregado_at is not None
        return entregados

    async def _publish(self, entry: OutboxEntry) -> OutboxEntry:
        event = entry.event
        try:
            await self._publisher.publish(str(event.id), event.tipo, event.to_payload())
        except PublishFailed as exc:
            logger.warning("event %s not delivered: %s", event.id, exc)
            return entry.fallido(str(exc), self._clock.now())
        return entry.entregado(self._clock.now())

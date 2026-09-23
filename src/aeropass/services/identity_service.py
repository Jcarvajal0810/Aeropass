"""US3 — creates the IdentidadDigital and enqueues ``credencial.emitida`` (FR-011..FR-013)."""

from __future__ import annotations

from aeropass.domain.enums import ResultadoIntento
from aeropass.domain.events import CredencialEmitidaV1, DomainEvent, OutboxEntry
from aeropass.domain.identity import IdentidadDigital
from aeropass.domain.ids import new_id
from aeropass.domain.passenger import Pasajero
from aeropass.domain.verification import IntentoVerificacion
from aeropass.observability.hooks import traced
from aeropass.ports.clock import Clock
from aeropass.ports.repositories import UnitOfWork


class IdentityService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @traced("identity.create_for_success")
    async def create_for_success(
        self, uow: UnitOfWork, pasajero: Pasajero, intento: IntentoVerificacion
    ) -> tuple[IdentidadDigital, DomainEvent | None]:
        """Runs inside the caller's transaction: identity + outbox row commit together.

        Idempotent: if the passenger already has an ACTIVE identity it is returned and no new
        event is produced.
        """
        assert intento.resultado is ResultadoIntento.EXITOSO
        existente = await uow.identities.get_active(pasajero.id)
        if existente is not None:
            return existente, None

        ahora = self._clock.now()
        identidad = IdentidadDigital.crear(
            id=new_id(), pasajero_id=pasajero.id, intento_origen_id=intento.id, ahora=ahora
        )
        event = CredencialEmitidaV1.from_identity(identidad, id=new_id(), ahora=ahora)
        await uow.identities.add(identidad)
        await uow.outbox.add(OutboxEntry.pendiente(event))
        return identidad, event

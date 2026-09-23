"""In-memory repositories + unit of work, for service unit tests only (never at runtime).

Each unit of work works on a deep copy of the committed state and publishes it on ``commit()``,
so tests observe the same all-or-nothing behaviour as Postgres. Uniqueness rules mirror the SQL
constraints.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Self

from aeropass.domain.credential.credential import CredencialAcceso, Transicion
from aeropass.domain.enums import (
    EstadoCredencial,
    EstadoEvento,
    EstadoPasajero,
    ResultadoIntento,
    TipoDocumento,
)
from aeropass.domain.events import OutboxEntry
from aeropass.domain.identity import IdentidadDigital
from aeropass.domain.passenger import Pasajero
from aeropass.domain.verification import IntentoVerificacion
from aeropass.ports.repositories import UniqueViolation


@dataclass
class InMemoryState:
    passengers: dict[uuid.UUID, Pasajero] = field(default_factory=dict)
    attempts: list[IntentoVerificacion] = field(default_factory=list)
    identities: dict[uuid.UUID, IdentidadDigital] = field(default_factory=dict)
    outbox: dict[uuid.UUID, OutboxEntry] = field(default_factory=dict)
    credentials: dict[uuid.UUID, CredencialAcceso] = field(default_factory=dict)
    transitions: dict[uuid.UUID, list[Transicion]] = field(default_factory=dict)


class InMemoryPassengerRepository:
    def __init__(self, state: InMemoryState) -> None:
        self._s = state

    async def get(self, id: uuid.UUID) -> Pasajero | None:
        return self._s.passengers.get(id)

    async def get_by_clerk_user(self, clerk_user_id: str) -> Pasajero | None:
        return next(
            (p for p in self._s.passengers.values() if p.clerk_user_id == clerk_user_id), None
        )

    async def get_by_document(self, tipo: TipoDocumento, numero: str) -> Pasajero | None:
        return next(
            (
                p
                for p in self._s.passengers.values()
                if p.documento.tipo == tipo and p.documento.numero == numero
            ),
            None,
        )

    async def get_for_update(self, id: uuid.UUID) -> Pasajero | None:
        return self._s.passengers.get(id)

    async def add(self, pasajero: Pasajero) -> None:
        if await self.get_by_clerk_user(pasajero.clerk_user_id):
            raise UniqueViolation("uq_pasajeros_clerk_user_id")
        if await self.get_by_document(pasajero.documento.tipo, pasajero.documento.numero):
            raise UniqueViolation("uq_pasajeros_documento")
        self._s.passengers[pasajero.id] = pasajero

    async def save(self, pasajero: Pasajero) -> None:
        self._s.passengers[pasajero.id] = pasajero

    async def list_by_estado(self, estado: EstadoPasajero, limit: int = 100) -> list[Pasajero]:
        return [p for p in self._s.passengers.values() if p.estado == estado][:limit]


class InMemoryVerificationAttemptRepository:
    def __init__(self, state: InMemoryState) -> None:
        self._s = state

    async def add(self, intento: IntentoVerificacion) -> None:
        self._s.attempts.append(intento)

    async def count_failed(self, pasajero_id: uuid.UUID) -> int:
        return sum(
            1
            for a in self._s.attempts
            if a.pasajero_id == pasajero_id and a.resultado is ResultadoIntento.FALLIDO
        )


class InMemoryIdentityRepository:
    def __init__(self, state: InMemoryState) -> None:
        self._s = state

    async def add(self, identidad: IdentidadDigital) -> None:
        if identidad.activa and await self.get_active(identidad.pasajero_id):
            raise UniqueViolation("uq_identidades_digitales_una_activa")
        self._s.identities[identidad.id] = identidad

    async def get_active(self, pasajero_id: uuid.UUID) -> IdentidadDigital | None:
        return next(
            (i for i in self._s.identities.values() if i.pasajero_id == pasajero_id and i.activa),
            None,
        )


class InMemoryOutboxRepository:
    def __init__(self, state: InMemoryState) -> None:
        self._s = state

    async def add(self, entry: OutboxEntry) -> None:
        self._s.outbox[entry.event.id] = entry

    async def claim_due(
        self,
        now: datetime,
        lease_until: datetime,
        limit: int,
        ids: Sequence[uuid.UUID] | None = None,
    ) -> list[OutboxEntry]:
        due = sorted(
            (
                e
                for e in self._s.outbox.values()
                if e.estado is EstadoEvento.PENDIENTE
                and e.proximo_intento_at <= now
                and (ids is None or e.event.id in ids)
            ),
            key=lambda e: e.proximo_intento_at,
        )[:limit]
        for e in due:
            self._s.outbox[e.event.id] = replace(e, proximo_intento_at=lease_until)
        return due

    async def save(self, entry: OutboxEntry) -> None:
        self._s.outbox[entry.event.id] = entry

    async def count_pending(self) -> int:
        return sum(1 for e in self._s.outbox.values() if e.estado is EstadoEvento.PENDIENTE)


_LIVE = (EstadoCredencial.EMITIDA, EstadoCredencial.ACTIVA)


class InMemoryCredentialRepository:
    def __init__(self, state: InMemoryState) -> None:
        self._s = state

    def _store(self, credencial: CredencialAcceso) -> None:
        self._s.transitions.setdefault(credencial.id, []).extend(credencial.pending_transitions)
        credencial.pending_transitions.clear()
        self._s.credentials[credencial.id] = copy.deepcopy(credencial)

    async def add(self, credencial: CredencialAcceso) -> None:
        if credencial.estado in _LIVE and await self.get_live_for_update(
            credencial.pasajero_id, credencial.codigo_vuelo
        ):
            raise UniqueViolation("uq_credenciales_acceso_una_viva")
        self._store(credencial)

    async def save(self, credencial: CredencialAcceso) -> None:
        self._store(credencial)

    async def get_for_update(self, id: uuid.UUID) -> CredencialAcceso | None:
        found = self._s.credentials.get(id)
        return copy.deepcopy(found) if found else None

    async def get_live_for_update(
        self, pasajero_id: uuid.UUID, codigo_vuelo: str
    ) -> CredencialAcceso | None:
        return next(
            (
                copy.deepcopy(c)
                for c in self._s.credentials.values()
                if c.pasajero_id == pasajero_id
                and c.codigo_vuelo == codigo_vuelo
                and c.estado in _LIVE
            ),
            None,
        )

    async def expired_live_for_update(self, now: datetime, limit: int) -> list[CredencialAcceso]:
        return [
            copy.deepcopy(c)
            for c in self._s.credentials.values()
            if c.estado in _LIVE and c.expira_at <= now
        ][:limit]

    async def history(self, id: uuid.UUID) -> list[Transicion]:
        return list(self._s.transitions.get(id, []))


class InMemoryUnitOfWork:
    def __init__(self, committed: InMemoryState | None = None) -> None:
        self.committed = committed or InMemoryState()
        self.commits = 0

    def __call__(self) -> Self:
        """Allows passing the instance itself as a ``uow_factory``."""
        return self

    async def __aenter__(self) -> Self:
        self._working = copy.deepcopy(self.committed)
        self.passengers = InMemoryPassengerRepository(self._working)
        self.attempts = InMemoryVerificationAttemptRepository(self._working)
        self.identities = InMemoryIdentityRepository(self._working)
        self.outbox = InMemoryOutboxRepository(self._working)
        self.credentials = InMemoryCredentialRepository(self._working)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._working = copy.deepcopy(self.committed)

    async def commit(self) -> None:
        self.committed.__dict__.update(copy.deepcopy(self._working).__dict__)
        self.commits += 1

    async def rollback(self) -> None:
        self._working = copy.deepcopy(self.committed)

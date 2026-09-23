"""Persistence ports. Services depend on these protocols, never on SQLAlchemy."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, Self

from aeropass.domain.credential.credential import CredencialAcceso, Transicion
from aeropass.domain.enums import EstadoPasajero, TipoDocumento
from aeropass.domain.events import OutboxEntry
from aeropass.domain.identity import IdentidadDigital
from aeropass.domain.passenger import Pasajero
from aeropass.domain.verification import IntentoVerificacion


class UniqueViolation(Exception):
    """A uniqueness constraint rejected the write (concurrent request won the race)."""

    def __init__(self, constraint: str) -> None:
        super().__init__(constraint)
        self.constraint = constraint


class PassengerRepository(Protocol):
    async def get(self, id: uuid.UUID) -> Pasajero | None: ...

    async def get_by_clerk_user(self, clerk_user_id: str) -> Pasajero | None: ...

    async def get_by_document(self, tipo: TipoDocumento, numero: str) -> Pasajero | None: ...

    async def get_for_update(self, id: uuid.UUID) -> Pasajero | None:
        """Load and lock the row until the transaction ends."""
        ...

    async def add(self, pasajero: Pasajero) -> None:
        """Insert; raises ``UniqueViolation`` on duplicate account or document."""
        ...

    async def save(self, pasajero: Pasajero) -> None: ...

    async def list_by_estado(self, estado: EstadoPasajero, limit: int = 100) -> list[Pasajero]: ...


class VerificationAttemptRepository(Protocol):
    async def add(self, intento: IntentoVerificacion) -> None: ...

    async def count_failed(self, pasajero_id: uuid.UUID) -> int: ...


class IdentityRepository(Protocol):
    async def add(self, identidad: IdentidadDigital) -> None:
        """Raises ``UniqueViolation`` if the passenger already has an ACTIVE identity."""
        ...

    async def get_active(self, pasajero_id: uuid.UUID) -> IdentidadDigital | None: ...


class OutboxRepository(Protocol):
    async def add(self, entry: OutboxEntry) -> None: ...

    async def claim_due(
        self,
        now: datetime,
        lease_until: datetime,
        limit: int,
        ids: Sequence[uuid.UUID] | None = None,
    ) -> list[OutboxEntry]:
        """Lock due PENDING entries (``FOR UPDATE SKIP LOCKED``) and push their next attempt to
        ``lease_until`` so no other dispatcher picks them while this one publishes."""
        ...

    async def save(self, entry: OutboxEntry) -> None: ...

    async def count_pending(self) -> int: ...


class CredentialRepository(Protocol):
    async def add(self, credencial: CredencialAcceso) -> None:
        """Insert the credential and flush its ``pending_transitions`` to the history."""
        ...

    async def save(self, credencial: CredencialAcceso) -> None:
        """Update state and append ``pending_transitions`` (accepted AND rejected)."""
        ...

    async def get_for_update(self, id: uuid.UUID) -> CredencialAcceso | None: ...

    async def get_live_for_update(
        self, pasajero_id: uuid.UUID, codigo_vuelo: str
    ) -> CredencialAcceso | None:
        """The EMITIDA/ACTIVA credential for this passenger and flight, locked."""
        ...

    async def expired_live_for_update(self, now: datetime, limit: int) -> list[CredencialAcceso]:
        """Live credentials past ``expira_at``, locked with SKIP LOCKED."""
        ...

    async def history(self, id: uuid.UUID) -> list[Transicion]: ...


class UnitOfWork(Protocol):
    """One business transaction. Leaving the context without ``commit()`` rolls back."""

    # Read-only members so concrete units of work may expose concrete repository types.
    @property
    def passengers(self) -> PassengerRepository: ...

    @property
    def attempts(self) -> VerificationAttemptRepository: ...

    @property
    def identities(self) -> IdentityRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    @property
    def credentials(self) -> CredentialRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

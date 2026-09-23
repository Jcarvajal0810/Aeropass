"""Domain events and their outbox envelope (contracts/events/*.json, research R7).

New event types are new ``DomainEvent`` factories with their own versioned schema; the
publisher and the dispatcher never change (open/closed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from aeropass.domain.enums import EstadoEvento
from aeropass.domain.identity import IdentidadDigital

MAX_BACKOFF_SECONDS = 180  # worst case after recovery: 180 s + 60 s schedule < 5 min (SC-006)


@dataclass(frozen=True)
class DomainEvent:
    id: uuid.UUID
    tipo: str
    version: int
    ocurrido_at: datetime
    datos: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tipo": self.tipo,
            "version": self.version,
            "ocurrido_at": self.ocurrido_at.isoformat().replace("+00:00", "Z"),
            "datos": self.datos,
        }


class CredencialEmitidaV1:
    TIPO = "credencial.emitida"
    VERSION = 1

    @classmethod
    def from_identity(
        cls, identidad: IdentidadDigital, *, id: uuid.UUID, ahora: datetime
    ) -> DomainEvent:
        return DomainEvent(
            id=id,
            tipo=cls.TIPO,
            version=cls.VERSION,
            ocurrido_at=ahora,
            datos={
                "tipo_credencial": "IDENTIDAD_DIGITAL",
                "pasajero_id": str(identidad.pasajero_id),
                "identidad_id": str(identidad.id),
                "intento_id": str(identidad.intento_origen_id),
            },
        )


def backoff_seconds(intentos: int) -> int:
    return int(min(2**intentos, MAX_BACKOFF_SECONDS))


@dataclass(frozen=True)
class OutboxEntry:
    """A domain event waiting for (or after) delivery. The outbox row IS the source of truth."""

    event: DomainEvent
    estado: EstadoEvento
    intentos: int
    proximo_intento_at: datetime
    ultimo_error: str | None = None
    entregado_at: datetime | None = None

    @classmethod
    def pendiente(cls, event: DomainEvent) -> OutboxEntry:
        return cls(event, EstadoEvento.PENDIENTE, 0, event.ocurrido_at)

    def entregado(self, ahora: datetime) -> OutboxEntry:
        return replace(self, estado=EstadoEvento.ENTREGADO, entregado_at=ahora, ultimo_error=None)

    def fallido(self, error: str, ahora: datetime) -> OutboxEntry:
        intentos = self.intentos + 1
        return replace(
            self,
            intentos=intentos,
            ultimo_error=error[:500],
            proximo_intento_at=ahora + timedelta(seconds=backoff_seconds(intentos)),
        )

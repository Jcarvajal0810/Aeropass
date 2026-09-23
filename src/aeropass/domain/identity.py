"""IdentidadDigital (FR-011)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from aeropass.domain.enums import EstadoIdentidad


@dataclass(frozen=True)
class IdentidadDigital:
    id: uuid.UUID
    pasajero_id: uuid.UUID
    intento_origen_id: uuid.UUID
    estado: EstadoIdentidad
    created_at: datetime
    revocada_at: datetime | None = None

    @classmethod
    def crear(
        cls,
        *,
        id: uuid.UUID,
        pasajero_id: uuid.UUID,
        intento_origen_id: uuid.UUID,
        ahora: datetime,
    ) -> IdentidadDigital:
        return cls(id, pasajero_id, intento_origen_id, EstadoIdentidad.ACTIVA, ahora)

    @property
    def activa(self) -> bool:
        return self.estado is EstadoIdentidad.ACTIVA

    def revocar(self, ahora: datetime) -> IdentidadDigital:
        return replace(self, estado=EstadoIdentidad.REVOCADA, revocada_at=ahora)

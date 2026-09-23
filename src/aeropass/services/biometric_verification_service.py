"""US2 — records a verification attempt and its effect on the passenger (FR-006, FR-008)."""

from __future__ import annotations

import uuid

from aeropass.domain.images import StoredMedia
from aeropass.domain.passenger import Pasajero
from aeropass.domain.verification import BiometricResult, IntentoVerificacion, Thresholds
from aeropass.observability.hooks import traced
from aeropass.ports.clock import Clock
from aeropass.ports.repositories import UnitOfWork


class BiometricVerificationService:
    def __init__(self, thresholds: Thresholds, clock: Clock) -> None:
        self._thresholds = thresholds
        self._clock = clock

    @traced("verification.record_attempt")
    async def record_attempt(
        self,
        uow: UnitOfWork,
        pasajero: Pasajero,
        *,
        intento_id: uuid.UUID,
        selfie: StoredMedia,
        result: BiometricResult | None,
        proveedor: str,
    ) -> IntentoVerificacion:
        """Adds the attempt and updates the (locked) passenger in the caller's transaction."""
        ahora = self._clock.now()
        intento = IntentoVerificacion.evaluar(
            id=intento_id,
            pasajero_id=pasajero.id,
            selfie=selfie,
            result=result,
            umbrales=self._thresholds,
            proveedor=proveedor,
            ahora=ahora,
        )
        pasajero.apply_outcome(intento.resultado, ahora)
        await uow.attempts.add(intento)
        await uow.passengers.save(pasajero)
        return intento

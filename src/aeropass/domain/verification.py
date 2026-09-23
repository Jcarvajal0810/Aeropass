"""Biometric verification rules (single source for FR-006/FR-007)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from aeropass.domain.enums import MotivoFallo, ResultadoIntento
from aeropass.domain.images import StoredMedia


@dataclass(frozen=True)
class BiometricResult:
    score_liveness: float
    score_comparacion: float
    proveedor: str


@dataclass(frozen=True)
class Thresholds:
    liveness: float
    comparacion: float


def evaluate_outcome(
    result: BiometricResult | None, thresholds: Thresholds
) -> tuple[ResultadoIntento, MotivoFallo | None]:
    """EXITOSO only if BOTH scores reach their (inclusive) threshold; liveness failure wins."""
    if result is None:
        return ResultadoIntento.NO_CONCLUYENTE, None
    if result.score_liveness < thresholds.liveness:
        return ResultadoIntento.FALLIDO, MotivoFallo.LIVENESS
    if result.score_comparacion < thresholds.comparacion:
        return ResultadoIntento.FALLIDO, MotivoFallo.COMPARACION
    return ResultadoIntento.EXITOSO, None


@dataclass(frozen=True)
class IntentoVerificacion:
    id: uuid.UUID
    pasajero_id: uuid.UUID
    selfie: StoredMedia
    resultado: ResultadoIntento
    motivo_fallo: MotivoFallo | None
    score_liveness: float | None
    score_comparacion: float | None
    umbrales: Thresholds
    proveedor: str
    created_at: datetime

    @classmethod
    def evaluar(
        cls,
        *,
        id: uuid.UUID,
        pasajero_id: uuid.UUID,
        selfie: StoredMedia,
        result: BiometricResult | None,
        umbrales: Thresholds,
        proveedor: str,
        ahora: datetime,
    ) -> IntentoVerificacion:
        resultado, motivo = evaluate_outcome(result, umbrales)
        return cls(
            id=id,
            pasajero_id=pasajero_id,
            selfie=selfie,
            resultado=resultado,
            motivo_fallo=motivo,
            score_liveness=result.score_liveness if result else None,
            score_comparacion=result.score_comparacion if result else None,
            umbrales=umbrales,
            proveedor=result.proveedor if result else proveedor,
            created_at=ahora,
        )

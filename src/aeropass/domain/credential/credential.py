"""CredencialAcceso aggregate (FR-016..FR-019).

Transition methods NEVER raise for an invalid transition: they return a ``TransitionResult`` and
record the attempt in ``pending_transitions`` so the repository persists rejected attempts too
(a raised exception would roll the history row back — FR-019, analysis finding I1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from aeropass.domain.credential.states import state_for
from aeropass.domain.enums import EstadoCredencial

SISTEMA = "sistema"

MOTIVO_EMISION = "EMISION"
MOTIVO_ACTIVACION = "ACTIVACION"
MOTIVO_CONSUMO = "CONSUMO"
MOTIVO_EXPIRACION = "EXPIRACION"
MOTIVO_RENOVACION = "RENOVACION"
MOTIVO_INVALIDA = "TRANSICION_INVALIDA"


@dataclass(frozen=True)
class Transicion:
    estado_anterior: EstadoCredencial | None
    estado_solicitado: EstadoCredencial
    aceptada: bool
    motivo: str
    actor: str
    created_at: datetime


@dataclass(frozen=True)
class TransitionResult:
    aceptada: bool
    estado_actual: EstadoCredencial


@dataclass
class CredencialAcceso:
    id: uuid.UUID  # also the token's jti
    pasajero_id: uuid.UUID
    identidad_id: uuid.UUID
    codigo_vuelo: str
    permisos: tuple[str, ...]
    firma: str
    kid: str
    emitida_at: datetime
    expira_at: datetime
    estado: EstadoCredencial
    updated_at: datetime
    pending_transitions: list[Transicion] = field(default_factory=list)

    @classmethod
    def emitir(
        cls,
        *,
        id: uuid.UUID,
        pasajero_id: uuid.UUID,
        identidad_id: uuid.UUID,
        codigo_vuelo: str,
        permisos: tuple[str, ...],
        firma: str,
        kid: str,
        emitida_at: datetime,
        expira_at: datetime,
    ) -> CredencialAcceso:
        c = cls(
            id=id,
            pasajero_id=pasajero_id,
            identidad_id=identidad_id,
            codigo_vuelo=codigo_vuelo,
            permisos=permisos,
            firma=firma,
            kid=kid,
            emitida_at=emitida_at,
            expira_at=expira_at,
            estado=EstadoCredencial.EMITIDA,
            updated_at=emitida_at,
        )
        c.pending_transitions.append(
            Transicion(None, EstadoCredencial.EMITIDA, True, MOTIVO_EMISION, SISTEMA, emitida_at)
        )
        return c

    # --- transitions -----------------------------------------------------------------------
    def activar(self, ahora: datetime) -> TransitionResult:
        return self._transition(EstadoCredencial.ACTIVA, MOTIVO_ACTIVACION, SISTEMA, ahora)

    def consumir(self, ahora: datetime, *, actor: str) -> TransitionResult:
        return self._transition(EstadoCredencial.CONSUMIDA, MOTIVO_CONSUMO, actor, ahora)

    def expirar(self, ahora: datetime) -> TransitionResult:
        return self._transition(EstadoCredencial.EXPIRADA, MOTIVO_EXPIRACION, SISTEMA, ahora)

    def revocar(self, motivo: str, ahora: datetime, *, actor: str = SISTEMA) -> TransitionResult:
        return self._transition(EstadoCredencial.REVOCADA, motivo, actor, ahora)

    def _transition(
        self, target: EstadoCredencial, motivo: str, actor: str, ahora: datetime
    ) -> TransitionResult:
        anterior = self.estado
        aceptada = state_for(anterior).can_transition_to(target)
        if aceptada:
            self.estado = target
            self.updated_at = ahora
        self.pending_transitions.append(
            Transicion(
                anterior, target, aceptada, motivo if aceptada else MOTIVO_INVALIDA, actor, ahora
            )
        )
        return TransitionResult(aceptada=aceptada, estado_actual=self.estado)

    # --- queries ----------------------------------------------------------------------------
    def is_expired(self, now: datetime) -> bool:
        return now >= self.expira_at

    @property
    def is_final(self) -> bool:
        return state_for(self.estado).is_final

    def refresh_expiry(self, now: datetime) -> bool:
        """Lazy expiry: moves a live, past-due credential to EXPIRADA. Returns True if it did."""
        if not self.is_final and self.is_expired(now):
            return self.expirar(now).aceptada
        return False

    @property
    def ttl_seconds(self) -> int:
        return int((self.expira_at - self.emitida_at).total_seconds())

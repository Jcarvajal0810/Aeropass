"""State pattern for CredencialAcceso (constitution; data-model.md transition table).

Each state object knows which transitions it allows; there are no scattered ``if estado == …``
checks anywhere else (DRY). Final states allow nothing, which is how RN-06 is enforced.
"""

from __future__ import annotations

from abc import ABC

from aeropass.domain.enums import EstadoCredencial


class CredentialState(ABC):
    estado: EstadoCredencial
    allowed: frozenset[EstadoCredencial] = frozenset()

    def can_transition_to(self, target: EstadoCredencial) -> bool:
        return target in self.allowed

    @property
    def is_final(self) -> bool:
        return not self.allowed


class Emitida(CredentialState):
    estado = EstadoCredencial.EMITIDA
    allowed = frozenset(
        {EstadoCredencial.ACTIVA, EstadoCredencial.EXPIRADA, EstadoCredencial.REVOCADA}
    )


class Activa(CredentialState):
    estado = EstadoCredencial.ACTIVA
    allowed = frozenset(
        {EstadoCredencial.CONSUMIDA, EstadoCredencial.EXPIRADA, EstadoCredencial.REVOCADA}
    )


class Consumida(CredentialState):
    estado = EstadoCredencial.CONSUMIDA  # final: CONSUMIDA → CONSUMIDA is rejected (RN-06)


class Expirada(CredentialState):
    estado = EstadoCredencial.EXPIRADA


class Revocada(CredentialState):
    estado = EstadoCredencial.REVOCADA


_STATES: dict[EstadoCredencial, CredentialState] = {
    s.estado: s for s in (Emitida(), Activa(), Consumida(), Expirada(), Revocada())
}


def state_for(estado: EstadoCredencial) -> CredentialState:
    return _STATES[estado]

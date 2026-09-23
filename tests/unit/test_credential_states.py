"""State pattern: every cell of the transition table in data-model.md (FR-019, RN-06)."""

from datetime import UTC, datetime, timedelta

import pytest

from aeropass.domain.credential.credential import CredencialAcceso
from aeropass.domain.credential.states import state_for
from aeropass.domain.enums import EstadoCredencial as E
from aeropass.domain.ids import new_id

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

ALLOWED = {
    (E.EMITIDA, E.ACTIVA),
    (E.EMITIDA, E.EXPIRADA),
    (E.EMITIDA, E.REVOCADA),
    (E.ACTIVA, E.CONSUMIDA),
    (E.ACTIVA, E.EXPIRADA),
    (E.ACTIVA, E.REVOCADA),
}
TARGETS = [E.ACTIVA, E.CONSUMIDA, E.EXPIRADA, E.REVOCADA]


def credencial(estado: E) -> CredencialAcceso:
    c = CredencialAcceso.emitir(
        id=new_id(),
        pasajero_id=new_id(),
        identidad_id=new_id(),
        codigo_vuelo="AV9380",
        permisos=("embarque",),
        firma="sig",
        kid="k1",
        emitida_at=NOW,
        expira_at=NOW + timedelta(seconds=45),
    )
    c.estado = estado
    c.pending_transitions.clear()
    return c


def apply(c: CredencialAcceso, target: E):
    return {
        E.ACTIVA: lambda: c.activar(NOW),
        E.CONSUMIDA: lambda: c.consumir(NOW, actor="checkpoint:test"),
        E.EXPIRADA: lambda: c.expirar(NOW),
        E.REVOCADA: lambda: c.revocar("RENOVACION", NOW),
    }[target]()


@pytest.mark.parametrize("origin", list(E))
@pytest.mark.parametrize("target", TARGETS)
def test_transition_table(origin, target):
    c = credencial(origin)
    result = apply(c, target)  # never raises, even when rejected

    expected = (origin, target) in ALLOWED
    assert result.aceptada is expected
    assert c.estado is (target if expected else origin)
    assert result.estado_actual is c.estado
    assert state_for(origin).can_transition_to(target) is expected

    (t,) = c.pending_transitions
    assert t.estado_anterior is origin
    assert t.estado_solicitado is target
    assert t.aceptada is expected
    if not expected:
        assert t.motivo == "TRANSICION_INVALIDA"


def test_rn06_consumed_cannot_be_consumed_again():
    c = credencial(E.ACTIVA)
    assert c.consumir(NOW, actor="checkpoint:1").aceptada
    second = c.consumir(NOW, actor="checkpoint:2")
    assert not second.aceptada
    assert c.estado is E.CONSUMIDA
    assert [t.aceptada for t in c.pending_transitions] == [True, False]
    assert c.pending_transitions[1].actor == "checkpoint:2"


@pytest.mark.parametrize("estado", [E.CONSUMIDA, E.EXPIRADA, E.REVOCADA])
def test_final_states(estado):
    assert state_for(estado).is_final
    assert not state_for(estado).allowed


def test_emitir_records_creation_transition():
    c = CredencialAcceso.emitir(
        id=new_id(),
        pasajero_id=new_id(),
        identidad_id=new_id(),
        codigo_vuelo="AV9380",
        permisos=("embarque",),
        firma="sig",
        kid="k1",
        emitida_at=NOW,
        expira_at=NOW + timedelta(seconds=45),
    )
    (t,) = c.pending_transitions
    assert t.estado_anterior is None
    assert t.estado_solicitado is E.EMITIDA
    assert t.motivo == "EMISION"


def test_is_expired():
    c = credencial(E.ACTIVA)
    assert not c.is_expired(NOW + timedelta(seconds=44))
    assert c.is_expired(NOW + timedelta(seconds=45))

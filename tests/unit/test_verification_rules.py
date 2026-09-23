from datetime import UTC, date, datetime

import pytest

from aeropass.domain.enums import EstadoPasajero, MotivoFallo, ResultadoIntento, TipoDocumento
from aeropass.domain.errors import EstadoNoPermiteVerificacion
from aeropass.domain.ids import new_id
from aeropass.domain.images import StoredMedia
from aeropass.domain.passenger import MAX_INTENTOS_FALLIDOS, DatosDocumento, Pasajero
from aeropass.domain.verification import BiometricResult, Thresholds, evaluate_outcome

T = Thresholds(liveness=0.80, comparacion=0.80)
NOW = datetime(2026, 9, 23, tzinfo=UTC)


def result(liveness: float, comparacion: float) -> BiometricResult:
    return BiometricResult(score_liveness=liveness, score_comparacion=comparacion, proveedor="t")


@pytest.mark.parametrize(
    ("liveness", "comparacion", "expected"),
    [
        (0.80, 0.80, (ResultadoIntento.EXITOSO, None)),  # inclusive thresholds
        (0.95, 0.99, (ResultadoIntento.EXITOSO, None)),
        (0.79, 0.99, (ResultadoIntento.FALLIDO, MotivoFallo.LIVENESS)),
        (0.99, 0.79, (ResultadoIntento.FALLIDO, MotivoFallo.COMPARACION)),
        (0.10, 0.10, (ResultadoIntento.FALLIDO, MotivoFallo.LIVENESS)),  # liveness has priority
    ],
)
def test_evaluate_outcome(liveness, comparacion, expected):
    assert evaluate_outcome(result(liveness, comparacion), T) == expected


def test_no_result_is_inconclusive():
    assert evaluate_outcome(None, T) == (ResultadoIntento.NO_CONCLUYENTE, None)


def pasajero() -> Pasajero:
    datos = DatosDocumento.validar(
        nombre_completo="Ana Pérez",
        tipo_documento=TipoDocumento.CC,
        numero_documento="12345678",
        fecha_vencimiento=date(2030, 1, 1),
        hoy=NOW.date(),
    )
    return Pasajero.registrar(
        id=new_id(), clerk_user_id="u", datos=datos, foto=StoredMedia("u", "p"), ahora=NOW
    )


def test_success_verifies():
    p = pasajero()
    p.apply_outcome(ResultadoIntento.EXITOSO, NOW)
    assert p.estado is EstadoPasajero.VERIFICADO
    assert p.intentos_fallidos == 0


def test_failures_count_and_third_requires_manual_review():
    p = pasajero()
    for i in range(1, MAX_INTENTOS_FALLIDOS):
        p.apply_outcome(ResultadoIntento.FALLIDO, NOW)
        assert p.estado is EstadoPasajero.PENDIENTE_VERIFICACION
        assert p.intentos_fallidos == i
        assert p.intentos_restantes == MAX_INTENTOS_FALLIDOS - i
    p.apply_outcome(ResultadoIntento.FALLIDO, NOW)
    assert p.estado is EstadoPasajero.REQUIERE_REVISION_MANUAL
    assert p.intentos_restantes == 0


def test_inconclusive_does_not_count():
    p = pasajero()
    p.apply_outcome(ResultadoIntento.NO_CONCLUYENTE, NOW)
    assert p.intentos_fallidos == 0
    assert p.estado is EstadoPasajero.PENDIENTE_VERIFICACION


@pytest.mark.parametrize(
    "estado", [EstadoPasajero.VERIFICADO, EstadoPasajero.REQUIERE_REVISION_MANUAL]
)
def test_final_states_reject_new_attempts(estado):
    p = pasajero()
    p.estado = estado
    with pytest.raises(EstadoNoPermiteVerificacion):
        p.assert_can_verify()
    with pytest.raises(EstadoNoPermiteVerificacion):
        p.apply_outcome(ResultadoIntento.EXITOSO, NOW)

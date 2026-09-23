from datetime import UTC, datetime, timedelta

import pytest

from aeropass.domain.credential.builder import CredencialAccesoBuilder
from aeropass.domain.credential.signing import CredentialSigner
from aeropass.domain.enums import EstadoCredencial
from aeropass.domain.errors import DatosInvalidos
from aeropass.domain.ids import new_id

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
SIGNER = CredentialSigner.generate(kid="test")


STEPS = {
    "para_pasajero": lambda b: b.para_pasajero(new_id()),
    "con_identidad": lambda b: b.con_identidad(new_id()),
    "para_vuelo": lambda b: b.para_vuelo("AV9380"),
    "con_ttl": lambda b: b.con_ttl(45),
    "firmado_con": lambda b: b.firmado_con(SIGNER),
    "emitido_en": lambda b: b.emitido_en(NOW),
}


def complete(skip: str | None = None) -> CredencialAccesoBuilder:
    builder = CredencialAccesoBuilder()
    for name, step in STEPS.items():
        if name != skip:
            step(builder)
    return builder


def test_builds_emitted_credential_with_defaults():
    credencial, token = complete().build()
    assert credencial.estado is EstadoCredencial.EMITIDA
    assert credencial.permisos == ("embarque",)
    assert credencial.expira_at == NOW + timedelta(seconds=45)
    assert credencial.kid == "test"
    assert token.endswith(credencial.firma)


def test_custom_permissions():
    credencial, _ = complete().con_permisos(["embarque", "sala_vip"]).build()
    assert credencial.permisos == ("embarque", "sala_vip")


@pytest.mark.parametrize("step", list(STEPS))
def test_missing_part_fails(step):
    with pytest.raises(DatosInvalidos):
        complete(skip=step).build()


@pytest.mark.parametrize("ttl", [29, 61, 0])
def test_ttl_out_of_range(ttl):
    with pytest.raises(DatosInvalidos):
        complete().con_ttl(ttl).build()


@pytest.mark.parametrize("ttl", [30, 60])
def test_ttl_bounds_inclusive(ttl):
    credencial, _ = complete().con_ttl(ttl).build()
    assert (credencial.expira_at - credencial.emitida_at).total_seconds() == ttl


@pytest.mark.parametrize("code", ["A", "AV", "AV12345", "AV-93", "A$123", ""])
def test_invalid_flight_code(code):
    with pytest.raises(DatosInvalidos):
        complete().para_vuelo(code).build()


def test_empty_permissions_rejected():
    with pytest.raises(DatosInvalidos):
        complete().con_permisos([]).build()


def test_flight_code_normalized():
    credencial, _ = complete().para_vuelo(" av9380 ").build()
    assert credencial.codigo_vuelo == "AV9380"

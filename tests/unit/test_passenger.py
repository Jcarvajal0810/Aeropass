from datetime import UTC, date, datetime

import pytest

from aeropass.domain.enums import EstadoPasajero, TipoDocumento
from aeropass.domain.errors import DatosInvalidos, DocumentoVencido
from aeropass.domain.ids import new_id
from aeropass.domain.images import StoredMedia
from aeropass.domain.passenger import DatosDocumento, Pasajero, normalizar_numero

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
FOTO = StoredMedia(url="https://x/documentos/p/rostro.jpg", pathname="documentos/p/rostro.jpg")


def datos(**overrides) -> DatosDocumento:
    base = dict(
        nombre_completo="  Ana María Pérez  ",
        tipo_documento=TipoDocumento.CC,
        numero_documento="1.020-345 678",
        fecha_vencimiento=date(2030, 1, 1),
    )
    base.update(overrides)
    return DatosDocumento.validar(**base, hoy=NOW.date())


def test_normalizes_document_number():
    assert normalizar_numero(" ab-12.34 5 ") == "AB12345"


def test_valid_document_builds_pending_passenger():
    d = datos()
    p = Pasajero.registrar(id=new_id(), clerk_user_id="user_1", datos=d, foto=FOTO, ahora=NOW)
    assert p.estado is EstadoPasajero.PENDIENTE_VERIFICACION
    assert p.intentos_fallidos == 0
    assert p.documento.numero == "1020345678"
    assert p.documento.nombre_completo == "Ana María Pérez"
    assert p.documento.foto == FOTO


@pytest.mark.parametrize("numero", ["123", "A" * 21, "12#45", ""])
def test_invalid_document_number(numero):
    with pytest.raises(DatosInvalidos) as exc:
        datos(numero_documento=numero)
    assert "numero_documento" in exc.value.detalles["campos"]


def test_name_length():
    with pytest.raises(DatosInvalidos) as exc:
        datos(nombre_completo="A")
    assert exc.value.detalles["campos"] == ["nombre_completo"]


def test_expired_document_rejected():
    with pytest.raises(DocumentoVencido):
        datos(fecha_vencimiento=date(2026, 9, 22))


def test_document_expiring_today_is_valid():
    assert datos(fecha_vencimiento=NOW.date()).fecha_vencimiento == NOW.date()


def test_masked_number_shows_last_four():
    assert datos().numero_enmascarado == "******5678"


def test_same_document_comparison():
    d = datos()
    p = Pasajero.registrar(id=new_id(), clerk_user_id="u", datos=d, foto=FOTO, ahora=NOW)
    assert p.documento.es_mismo(datos(numero_documento="1020345678"))
    assert not p.documento.es_mismo(datos(numero_documento="999999"))

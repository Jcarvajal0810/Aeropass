"""US1 — Identity document registration (spec.md, scenarios 1–5 + edge cases)."""

import pytest
from sqlalchemy import text

from tests.integration.helpers import register

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def _count(session_factory, table: str) -> int:
    async with session_factory() as s:
        return (await s.execute(text(f"select count(*) from {table}"))).scalar_one()


async def test_scenario1_creates_pending_passenger_with_photo_reference(
    client, container, session_factory
):
    response = await register(client, "user_1")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["estado"] == "PENDIENTE_VERIFICACION"
    assert body["numero_documento_enmascarado"] == "******5678"
    assert body["identidad_id"] is None

    storage = container.media_storage
    assert len(storage.objects) == 1
    (pathname,) = storage.objects
    assert pathname.startswith(f"documentos/{body['id']}/rostro")
    async with session_factory() as s:
        row = (
            await s.execute(
                text("select foto_documento_blob_pathname, foto_documento_blob_url from pasajeros")
            )
        ).one()
    assert row[0] == pathname
    assert row[1].endswith(pathname)

    me = await client.get("/v1/identity/me", headers={"Authorization": "Bearer test:user_1"})
    assert me.status_code == 200
    assert me.json()["id"] == body["id"]


async def test_scenario2_expired_document_rejected_nothing_persisted(
    client, container, session_factory
):
    response = await register(client, "user_1", fecha_vencimiento="2020-01-01")
    assert response.status_code == 422
    assert response.json()["codigo"] == "DOCUMENTO_VENCIDO"
    assert await _count(session_factory, "pasajeros") == 0
    assert container.media_storage.objects == {}


async def test_scenario3_resubmission_same_account_returns_existing(client, container):
    first = await register(client, "user_1")
    again = await register(client, "user_1", numero_documento="1.020.345-678")
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(container.media_storage.objects) == 1  # the new photo is ignored


async def test_scenario4_document_of_another_account_rejected(client):
    await register(client, "user_1")
    response = await register(client, "user_2")
    assert response.status_code == 409
    assert response.json()["codigo"] == "DOCUMENTO_YA_REGISTRADO"


async def test_scenario5_account_with_other_document_rejected(client):
    await register(client, "user_1")
    response = await register(client, "user_1", numero_documento="555555")
    assert response.status_code == 409
    assert response.json()["codigo"] == "CUENTA_YA_REGISTRADA"


async def test_missing_fields_listed(client, session_factory):
    response = await client.post(
        "/v1/identity",
        data={"tipo_documento": "CC"},
        headers={"Authorization": "Bearer test:user_1"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["codigo"] == "DATOS_INVALIDOS"
    for campo in ("nombre_completo", "numero_documento", "fecha_vencimiento", "foto_documento"):
        assert campo in body["detalles"]["campos"]
    assert await _count(session_factory, "pasajeros") == 0


async def test_missing_photo_rejected(client, container):
    response = await register(client, "user_1", include_foto=False)
    assert response.status_code == 422
    assert "foto_documento" in response.json()["detalles"]["campos"]
    assert container.media_storage.objects == {}


async def test_photo_storage_failure_returns_503_and_persists_nothing(
    client, container, session_factory
):
    container.media_storage.fail_next_put = True
    response = await register(client, "user_1")
    assert response.status_code == 503
    assert response.json()["codigo"] == "ALMACENAMIENTO_NO_DISPONIBLE"
    assert "retry-after" in response.headers
    assert await _count(session_factory, "pasajeros") == 0


async def test_unsupported_photo_format(client):
    response = await register(client, "user_1", foto=b"GIF89a...", foto_type="image/gif")
    assert response.status_code == 415


async def test_requires_authentication(client):
    response = await client.get("/v1/identity/me")
    assert response.status_code == 401
    assert response.json()["codigo"] == "NO_AUTENTICADO"

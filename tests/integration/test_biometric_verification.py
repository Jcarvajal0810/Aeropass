"""US2 — Verificación biométrica con selfie (spec.md scenarios 1–5 + edge cases)."""

import asyncio

import pytest
from sqlalchemy import text

from aeropass.adapters.fakes.images import make_image, read_marker
from aeropass.domain.images import MAX_IMAGE_BYTES
from tests.integration.helpers import register, verify

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


@pytest.fixture
async def registered(client):
    response = await register(client, "u1")
    assert response.status_code == 201
    return response.json()


async def _attempts(session_factory):
    async with session_factory() as s:
        return (
            await s.execute(
                text(
                    "select resultado, motivo_fallo, score_liveness, score_comparacion, "
                    "selfie_blob_pathname, selfie_blob_url, proveedor, umbral_liveness "
                    "from intentos_verificacion order by created_at"
                )
            )
        ).all()


async def test_scenario1_success_stores_selfie_and_verifies(
    client, container, registered, session_factory
):
    response = await verify(client, "u1", "ok")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resultado"] == "EXITOSO"
    assert body["motivo_fallo"] is None
    assert body["estado_pasajero"] == "VERIFICADO"
    assert body["score_liveness"] >= 0.8 and body["score_comparacion"] >= 0.8

    (row,) = await _attempts(session_factory)
    assert row.resultado == "EXITOSO"
    assert row.proveedor == "mock"
    assert float(row.umbral_liveness) == pytest.approx(0.80)
    # only the reference is persisted; the bytes live in the media store
    stored = container.media_storage.objects[row.selfie_blob_pathname]
    assert read_marker(stored.data) == "ok"
    assert row.selfie_blob_pathname.startswith(f"selfies/{registered['id']}/{body['intento_id']}")
    assert row.selfie_blob_url.endswith(row.selfie_blob_pathname)


async def test_provider_receives_registered_document_photo_as_reference(
    client, container, registered
):
    await verify(client, "u1", "ok")
    reference = container.biometric_provider.inner.last_reference
    assert read_marker(reference.data) == "documento"


async def test_scenario2_liveness_failure(client, registered):
    body = (await verify(client, "u1", "spoof")).json()
    assert body["resultado"] == "FALLIDO"
    assert body["motivo_fallo"] == "LIVENESS"
    assert body["estado_pasajero"] == "PENDIENTE_VERIFICACION"
    assert body["intentos_restantes"] == 2


async def test_comparison_failure(client, registered):
    body = (await verify(client, "u1", "other")).json()
    assert body["motivo_fallo"] == "COMPARACION"


async def test_scenario3_three_failures_require_manual_review(client, registered):
    for marker in ("spoof", "other"):
        await verify(client, "u1", marker)
    body = (await verify(client, "u1", "spoof")).json()
    assert body["estado_pasajero"] == "REQUIERE_REVISION_MANUAL"
    assert body["intentos_restantes"] == 0
    blocked = await verify(client, "u1", "ok")
    assert blocked.status_code == 409
    assert blocked.json()["codigo"] == "ESTADO_NO_PERMITE_VERIFICACION"


async def test_scenario4_provider_down_is_inconclusive_and_not_counted(
    client, registered, session_factory
):
    body = (await verify(client, "u1", "timeout")).json()
    assert body["resultado"] == "NO_CONCLUYENTE"
    assert body["score_liveness"] is None
    assert body["reintentar_en_segundos"] == 30
    assert body["intentos_restantes"] == 3
    (row,) = await _attempts(session_factory)
    assert row.resultado == "NO_CONCLUYENTE"


async def test_scenario5_selfie_storage_failure_no_attempt(
    client, container, registered, session_factory
):
    container.media_storage.fail_next_put = True
    response = await verify(client, "u1", "ok")
    assert response.status_code == 503
    assert await _attempts(session_factory) == []


async def test_reference_photo_download_failure_no_attempt(
    client, container, registered, session_factory
):
    container.media_storage.fail_next_get = True
    response = await verify(client, "u1", "ok")
    assert response.status_code == 503
    assert await _attempts(session_factory) == []


async def test_unsupported_format(client, registered):
    response = await verify(client, "u1", content=b"GIF89a", content_type="image/gif")
    assert response.status_code == 415


async def test_too_large(client, registered):
    big = make_image("ok") + b"\x00" * MAX_IMAGE_BYTES
    assert (await verify(client, "u1", content=big)).status_code == 413


async def test_already_verified_rejected(client, registered):
    await verify(client, "u1", "ok")
    response = await verify(client, "u1", "ok")
    assert response.status_code == 409


async def test_not_registered(client):
    response = await verify(client, "nobody", "ok")
    assert response.status_code == 404
    assert response.json()["codigo"] == "PASAJERO_NO_REGISTRADO"


async def test_concurrent_selfies_only_first_success_counts(client, registered, session_factory):
    first, second = await asyncio.gather(verify(client, "u1", "ok"), verify(client, "u1", "ok"))
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409]
    assert len(await _attempts(session_factory)) == 1

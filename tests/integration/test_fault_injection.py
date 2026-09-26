"""Spec 003 — each fault through the real API: the answer a real outage would give.

Adapters are the fake ones (conftest), with ``FAULT_INJECTION_ENABLED``. §8 of the spec lists the
hypotheses; the tests named ``..._is_currently_unhandled`` record findings to decide on.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from aeropass.adapters.faults.wrappers import SigningUnavailable
from aeropass.config import Settings
from aeropass.observability.telemetry_catalog import FAULT_INJECTED
from tests.integration.helpers import issue_pass, register, verified_passenger, verify

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]

APPLIED = "x-aeropass-fault-applied"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        aeropass_adapters="fake",
        biometric_provider="mock",
        qr_ttl_seconds=45,
        biometric_timeout_seconds=0.3,
        fault_injection_enabled=True,
    )


def _fault(name: str) -> dict[str, str]:
    return {"X-AeroPass-Fault": name}


async def _verify_with(client, user_id: str, fault: str):
    from aeropass.adapters.fakes.images import make_image

    return await client.post(
        "/v1/biometrics/verifications",
        files={"selfie": ("selfie.jpg", make_image("ok"), "image/jpeg")},
        headers={"Authorization": f"Bearer test:{user_id}", **_fault(fault)},
    )


async def test_blob_down_on_the_selfie_is_the_storage_503(client, sentry_capture):
    assert (await register(client, "u1")).status_code == 201

    response = await _verify_with(client, "u1", "blob_down")

    assert response.status_code == 503
    assert response.json()["codigo"] == "ALMACENAMIENTO_NO_DISPONIBLE"
    assert response.headers["retry-after"]
    assert response.headers[APPLIED] == "blob_down"
    logs = sentry_capture.audit_logs(FAULT_INJECTED)
    assert [log["attributes"]["aeropass.fault"] for log in logs] == ["blob_down"]


async def test_blob_down_on_registration_is_the_storage_503(client):
    response = await client.post(
        "/v1/identity",
        data={
            "nombre_completo": "Ana María Pérez",
            "tipo_documento": "CC",
            "numero_documento": "1020345678",
            "fecha_vencimiento": "2030-01-01",
        },
        files={"foto_documento": ("documento.jpg", b"\xff\xd8\xff" + b"0" * 64, "image/jpeg")},
        headers={"Authorization": "Bearer test:u2", **_fault("blob_down")},
    )
    assert response.status_code == 503
    assert response.json()["codigo"] == "ALMACENAMIENTO_NO_DISPONIBLE"


@pytest.mark.parametrize("fault", ["mxface_down", "mxface_quota", "mxface_slow:1000"])
async def test_provider_faults_are_inconclusive_and_cost_no_attempt(client, fault):
    assert (await register(client, "u3")).status_code == 201

    response = await _verify_with(client, "u3", fault)

    assert response.status_code == 200
    assert response.json()["resultado"] == "NO_CONCLUYENTE"
    assert response.headers[APPLIED] == fault.split(":")[0]
    me = await client.get("/v1/identity/me", headers={"Authorization": "Bearer test:u3"})
    assert me.json()["intentos_fallidos"] == 0


async def test_five_provider_faults_open_the_real_breaker(client, sentry_capture):
    assert (await register(client, "u4")).status_code == 201
    for _ in range(5):
        await _verify_with(client, "u4", "mxface_down")

    # No fault asked now: the open circuit alone answers NO_CONCLUYENTE.
    body = (await verify(client, "u4", "ok")).json()

    assert body["resultado"] == "NO_CONCLUYENTE"
    assert sentry_capture.audit_logs("resilience.circuit_opened")


async def test_qstash_down_does_not_change_the_verification(client):
    assert (await register(client, "u5")).status_code == 201

    response = await _verify_with(client, "u5", "qstash_down")

    assert response.status_code == 200
    assert response.json()["resultado"] == "EXITOSO"


async def test_redis_down_refuses_the_pass_instead_of_opening_a_reuse_window(client):
    await verified_passenger(client, "u6")

    response = await client.post(
        "/v1/passes",
        json={"codigo_vuelo": "AV9380"},
        headers={"Authorization": "Bearer test:u6", **_fault("redis_down")},
    )

    # The limiter failed open; registering the token failed, so the credential was revoked.
    assert response.status_code == 503
    assert response.json()["codigo"] == "ALMACENAMIENTO_NO_DISPONIBLE"
    assert response.headers[APPLIED] == "redis_down"


async def test_db_down_is_currently_unhandled(client):
    # Finding (spec 003 §8): a lost database surfaces as an unhandled error (a 500, alert B1).
    # The app expects a controlled 5xx; decide whether to map it to a DomainError.
    with pytest.raises(OperationalError):
        await client.get(
            "/v1/identity/me", headers={"Authorization": "Bearer test:u7", **_fault("db_down")}
        )


async def test_signing_down_is_currently_unhandled(client):
    # Finding (spec 003 §8): issuance should answer a controlled error, not a 500.
    await verified_passenger(client, "u8")
    with pytest.raises(SigningUnavailable):
        await client.post(
            "/v1/passes",
            json={"codigo_vuelo": "AV9380"},
            headers={"Authorization": "Bearer test:u8", **_fault("signing_down")},
        )


async def test_db_down_makes_health_unavailable(client):
    response = await client.get("/health", headers=_fault("db_down"))
    assert response.status_code == 503
    assert response.json() == {"estado": "no_disponible"}


async def test_an_unknown_fault_changes_nothing(client):
    assert (await register(client, "u9")).status_code == 201

    response = await _verify_with(client, "u9", "teleport")

    assert response.status_code == 200
    assert response.json()["resultado"] == "EXITOSO"
    assert APPLIED not in response.headers


async def test_a_pass_issued_without_faults_is_unaffected(client):
    await verified_passenger(client, "u10")
    assert (await issue_pass(client, "u10")).status_code == 201

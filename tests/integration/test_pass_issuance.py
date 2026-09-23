"""US4 — Generación del QR dinámico (spec US4 scenarios 1–6 + edge cases)."""

import uuid
from datetime import datetime

import jwt
import pytest

from aeropass.services.credential_lifecycle_service import ConsumeResult
from tests.integration.helpers import issue_pass, verified_passenger

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]

AUTH = {"Authorization": "Bearer test:u1"}


async def _detail(client, credencial_id: str, user: str = "u1"):
    return await client.get(
        f"/v1/passes/{credencial_id}", headers={"Authorization": f"Bearer test:{user}"}
    )


async def test_scenario1_issue_signed_short_lived_pass(client, container):
    await verified_passenger(client, "u1")
    response = await issue_pass(client, "u1")
    assert response.status_code == 201, response.text
    body = response.json()

    emitida = datetime.fromisoformat(body["emitida_at"])
    expira = datetime.fromisoformat(body["expira_at"])
    assert 30 <= (expira - emitida).total_seconds() <= 60
    assert body["estado"] == "ACTIVA"
    assert body["permisos"] == ["embarque"]
    assert body["renovar_en_segundos"] == 40

    jwks = (await client.get("/.well-known/jwks.json")).json()
    (key,) = jwks["keys"]
    claims = jwt.decode(
        body["token"],
        jwt.PyJWK(key).key,
        algorithms=["EdDSA"],
        options={"verify_exp": False, "verify_iat": False},  # tokens use the fake clock
    )
    assert claims["jti"] == body["credencial_id"]
    assert claims["flt"] == "AV9380"
    assert claims["exp"] - claims["iat"] == 45

    assert await container.token_store.is_active(uuid.UUID(body["credencial_id"]))
    assert container.token_store.ttl(uuid.UUID(body["credencial_id"])) == 45

    detail = (await _detail(client, body["credencial_id"])).json()
    assert detail["estado"] == "ACTIVA"
    assert [(h["estado_anterior"], h["estado_solicitado"]) for h in detail["historial"]] == [
        (None, "EMITIDA"),
        ("EMITIDA", "ACTIVA"),
    ]


async def test_scenario2_without_identity_rejected(client):
    from tests.integration.helpers import register

    await register(client, "u1")
    response = await issue_pass(client, "u1")
    assert response.status_code == 403
    assert response.json()["codigo"] == "IDENTIDAD_NO_ACTIVA"


async def test_not_registered_rejected(client):
    response = await issue_pass(client, "ghost")
    assert response.status_code == 403
    assert response.json()["codigo"] == "IDENTIDAD_NO_ACTIVA"


async def test_scenario3_renewal_revokes_previous(client, container):
    await verified_passenger(client, "u1")
    first = (await issue_pass(client, "u1")).json()
    second = (await issue_pass(client, "u1")).json()
    assert second["credencial_id"] != first["credencial_id"]

    old = (await _detail(client, first["credencial_id"])).json()
    assert old["estado"] == "REVOCADA"
    assert old["historial"][-1]["motivo"] == "RENOVACION"
    assert not await container.token_store.is_active(uuid.UUID(first["credencial_id"]))
    assert await container.token_store.is_active(uuid.UUID(second["credencial_id"]))


async def test_renewal_survives_post_commit_revoke_failure(client, container):
    await verified_passenger(client, "u1")
    first = (await issue_pass(client, "u1")).json()
    container.token_store.fail_next_revoke = True
    second = await issue_pass(client, "u1")
    assert second.status_code == 201
    # Postgres already says REVOCADA; the stale Redis key just expires on its own (≤ 60 s).
    assert (await _detail(client, first["credencial_id"])).json()["estado"] == "REVOCADA"


async def test_scenario4_expired_pass(client, container, fake_clock):
    await verified_passenger(client, "u1")
    body = (await issue_pass(client, "u1")).json()
    fake_clock.advance(46)
    detail = (await _detail(client, body["credencial_id"])).json()
    assert detail["estado"] == "EXPIRADA"
    assert detail["historial"][-1]["motivo"] == "EXPIRACION"
    assert not await container.token_store.is_active(uuid.UUID(body["credencial_id"]))


async def test_scenario5_consumed_cannot_be_consumed_again(client, container):
    await verified_passenger(client, "u1")
    body = (await issue_pass(client, "u1")).json()
    jti = uuid.UUID(body["credencial_id"])
    lifecycle = container.lifecycle_service
    assert await lifecycle.consume(jti, actor="checkpoint:A") is ConsumeResult.CONSUMIDA
    assert await lifecycle.consume(jti, actor="checkpoint:B") is ConsumeResult.YA_CONSUMIDA

    historial = (await _detail(client, body["credencial_id"])).json()["historial"]
    assert historial[-1]["estado_solicitado"] == "CONSUMIDA"
    assert historial[-1]["aceptada"] is False
    assert historial[-2]["aceptada"] is True


async def test_scenario6_rate_limit(client):
    await verified_passenger(client, "u1")
    for _ in range(30):
        assert (await issue_pass(client, "u1")).status_code == 201
    response = await issue_pass(client, "u1")
    assert response.status_code == 429
    assert response.json()["codigo"] == "LIMITE_EMISION_EXCEDIDO"
    assert int(response.headers["retry-after"]) >= 1


async def test_document_expiring_between_registration_and_pass(client, fake_clock):
    await verified_passenger(client, "u1")  # document valid until 2030-01-01
    fake_clock.set(datetime.fromisoformat("2030-01-02T12:00:00+00:00"))
    response = await issue_pass(client, "u1")
    assert response.status_code == 403
    assert response.json()["codigo"] == "DOCUMENTO_VENCIDO"


async def test_invalid_flight_code(client):
    await verified_passenger(client, "u1")
    response = await issue_pass(client, "u1", codigo_vuelo="NOT-A-FLIGHT")
    assert response.status_code == 422


async def test_token_store_failure_revokes_and_returns_503(client, container):
    await verified_passenger(client, "u1")
    container.token_store.fail_next_register = True
    response = await issue_pass(client, "u1")
    assert response.status_code == 503


async def test_pass_of_another_passenger_is_not_found(client):
    await verified_passenger(client, "u1")
    await verified_passenger(client, "u2")
    body = (await issue_pass(client, "u1")).json()
    assert (await _detail(client, body["credencial_id"], user="u2")).status_code == 404


async def test_sweep_marks_expired(client, fake_clock):
    await verified_passenger(client, "u1")
    body = (await issue_pass(client, "u1")).json()
    fake_clock.advance(120)
    report = (
        await client.post("/internal/outbox/dispatch", headers={"Upstash-Signature": "test"})
    ).json()
    assert report["credenciales_expiradas"] == 1
    assert (await _detail(client, body["credencial_id"])).json()["estado"] == "EXPIRADA"

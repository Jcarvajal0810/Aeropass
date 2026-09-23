"""US4 — FR-019: rejected transitions are committed, not lost to a rollback."""

import uuid

import pytest
from sqlalchemy import text

from aeropass.services.credential_lifecycle_service import ConsumeResult
from tests.integration.helpers import issue_pass, verified_passenger

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def _rejected(session_factory, jti: uuid.UUID):
    async with session_factory() as s:
        return (
            await s.execute(
                text(
                    "select estado_anterior::text, estado_solicitado::text, motivo "
                    "from transiciones_credencial where credencial_id = :id and not aceptada"
                ),
                {"id": jti},
            )
        ).all()


async def test_consuming_expired_credential(client, container, fake_clock, session_factory):
    await verified_passenger(client, "u1")
    jti = uuid.UUID((await issue_pass(client, "u1")).json()["credencial_id"])
    fake_clock.advance(61)

    assert await container.lifecycle_service.consume(jti, actor="checkpoint:1") is (
        ConsumeResult.EXPIRADA
    )
    assert await _rejected(session_factory, jti) == [
        ("EXPIRADA", "CONSUMIDA", "TRANSICION_INVALIDA")
    ]


async def test_revoking_consumed_credential(client, container, session_factory):
    await verified_passenger(client, "u1")
    jti = uuid.UUID((await issue_pass(client, "u1")).json()["credencial_id"])
    lifecycle = container.lifecycle_service
    assert await lifecycle.consume(jti, actor="checkpoint:1") is ConsumeResult.CONSUMIDA

    result = await lifecycle.revoke_by_id(jti, motivo="REVOCACION_MANUAL")
    assert not result.aceptada
    assert await _rejected(session_factory, jti) == [
        ("CONSUMIDA", "REVOCADA", "TRANSICION_INVALIDA")
    ]


async def test_unknown_credential(container):
    assert await container.lifecycle_service.consume(uuid.uuid4(), actor="x") is (
        ConsumeResult.NO_ENCONTRADA
    )

"""US4 — SC-004: 50 concurrent consumptions of the same QR → exactly one accepted (RN-06)."""

import asyncio
import uuid
from collections import Counter

import pytest
from sqlalchemy import text

from aeropass.services.credential_lifecycle_service import ConsumeResult
from tests.integration.helpers import issue_pass, verified_passenger

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_fifty_concurrent_consumes(client, container, session_factory):
    await verified_passenger(client, "u1")
    jti = uuid.UUID((await issue_pass(client, "u1")).json()["credencial_id"])

    results = await asyncio.gather(
        *(container.lifecycle_service.consume(jti, actor=f"checkpoint:{i}") for i in range(50))
    )

    assert Counter(results) == {ConsumeResult.CONSUMIDA: 1, ConsumeResult.YA_CONSUMIDA: 49}
    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "select aceptada, count(*) from transiciones_credencial "
                    "where credencial_id = :id and estado_solicitado = 'CONSUMIDA' "
                    "group by aceptada"
                ),
                {"id": jti},
            )
        ).all()
    assert dict(rows) == {True: 1, False: 49}
    assert not await container.token_store.is_active(jti)

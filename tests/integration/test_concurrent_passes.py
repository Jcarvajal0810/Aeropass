"""US4 — two simultaneous emissions leave exactly one ACTIVA (FR-020)."""

import asyncio

import pytest
from sqlalchemy import text

from tests.integration.helpers import issue_pass, verified_passenger

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_simultaneous_emissions(client, session_factory):
    await verified_passenger(client, "u1")
    first, second = await asyncio.gather(issue_pass(client, "u1"), issue_pass(client, "u1"))
    assert first.status_code == second.status_code == 201

    async with session_factory() as s:
        estados = sorted(
            (await s.execute(text("select estado::text from credenciales_acceso"))).scalars()
        )
    assert estados == ["ACTIVA", "REVOCADA"]

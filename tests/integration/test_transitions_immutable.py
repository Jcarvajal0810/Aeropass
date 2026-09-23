"""US4 — FR-018: the transition history cannot be modified or deleted."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import issue_pass, verified_passenger

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


@pytest.mark.parametrize(
    "statement",
    [
        "update transiciones_credencial set aceptada = not aceptada",
        "delete from transiciones_credencial",
    ],
)
async def test_history_is_append_only(client, session_factory, statement):
    await verified_passenger(client, "u1")
    await issue_pass(client, "u1")
    async with session_factory() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text(statement))

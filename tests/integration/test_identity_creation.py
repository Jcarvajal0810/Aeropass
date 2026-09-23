"""US3 — IdentidadDigital + outbox in the same transaction (spec US3 scenarios 1 and 3)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aeropass.domain.ids import new_id
from tests.contract.openapi_helper import event_validator
from tests.integration.helpers import register, verify

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_scenario1_success_creates_identity_and_publishes_event(
    client, container, session_factory
):
    pasajero = (await register(client, "u1")).json()
    body = (await verify(client, "u1", "ok")).json()
    assert body["identidad_id"] is not None

    async with session_factory() as s:
        identidad = (
            await s.execute(
                text("select id, pasajero_id, intento_origen_id, estado from identidades_digitales")
            )
        ).one()
        evento = (
            await s.execute(text("select id, tipo, estado, payload from outbox_eventos"))
        ).one()
    assert str(identidad.id) == body["identidad_id"]
    assert str(identidad.pasajero_id) == pasajero["id"]
    assert str(identidad.intento_origen_id) == body["intento_id"]
    assert identidad.estado == "ACTIVA"
    assert evento.tipo == "credencial.emitida"
    assert evento.estado == "ENTREGADO"  # published right after commit

    published = container.event_publisher.inner.published
    assert len(published) == 1
    assert published[0].deduplication_id == str(evento.id)
    assert not list(event_validator("credencial.emitida.v1.json").iter_errors(published[0].payload))

    me = await client.get("/v1/identity/me", headers={"Authorization": "Bearer test:u1"})
    assert me.json()["identidad_id"] == body["identidad_id"]


async def test_failed_attempt_creates_no_identity(client, session_factory):
    await register(client, "u1")
    body = (await verify(client, "u1", "spoof")).json()
    assert body["identidad_id"] is None
    async with session_factory() as s:
        assert (await s.execute(text("select count(*) from identidades_digitales"))).scalar() == 0
        assert (await s.execute(text("select count(*) from outbox_eventos"))).scalar() == 0


async def test_scenario3_only_one_active_identity_per_passenger(client, session_factory):
    await register(client, "u1")
    body = (await verify(client, "u1", "ok")).json()
    # A second ACTIVE identity is rejected by the partial unique index.
    async with session_factory() as s:
        pasajero_id, intento_id = (
            await s.execute(
                text("select pasajero_id, intento_origen_id from identidades_digitales")
            )
        ).one()
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "insert into identidades_digitales (id, pasajero_id, intento_origen_id, "
                    "estado, created_at) values (:id, :p, :i, 'ACTIVA', now())"
                ),
                {"id": new_id(), "p": pasajero_id, "i": new_id()},
            )
    assert body["identidad_id"]

"""US3 — the event is not lost while QStash is down and arrives < 5 min after recovery (SC-006)."""

import pytest
from sqlalchemy import text

from tests.integration.helpers import register, verify

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]

DISPATCH = "/internal/outbox/dispatch"
SIGNED = {"Upstash-Signature": "test"}


async def _event(session_factory):
    async with session_factory() as s:
        return (
            await s.execute(
                text("select id, estado, intentos, proximo_intento_at from outbox_eventos")
            )
        ).one()


async def test_event_survives_outage_and_is_delivered_after_recovery(
    client, container, fake_clock, session_factory
):
    publisher = container.event_publisher.inner
    publisher.down = True
    await register(client, "u1")

    response = await verify(client, "u1", "ok")
    assert response.status_code == 200
    assert response.json()["identidad_id"] is not None
    event = await _event(session_factory)
    assert event.estado == "PENDIENTE"
    assert event.intentos == 1

    # 30-minute outage, QStash schedule firing every 60 s: backoff grows to its 180 s cap.
    for _ in range(30):
        fake_clock.advance(60)
        assert (await client.post(DISPATCH, headers=SIGNED)).status_code == 200
    event = await _event(session_factory)
    assert event.estado == "PENDIENTE"
    assert (event.proximo_intento_at - fake_clock.now()).total_seconds() <= 180

    publisher.down = False
    recovered_at = fake_clock.now()
    delivered = False
    while (fake_clock.now() - recovered_at).total_seconds() < 240:
        fake_clock.advance(60)
        await client.post(DISPATCH, headers=SIGNED)
        if (await _event(session_factory)).estado == "ENTREGADO":
            delivered = True
            break
    assert delivered, "event not delivered within 4 simulated minutes after recovery"
    assert publisher.published[-1].deduplication_id == str(event.id)
    assert len(publisher.published) == 1


async def test_dispatch_requires_valid_signature(client):
    assert (await client.post(DISPATCH)).status_code == 401
    assert (await client.post(DISPATCH, headers={"Upstash-Signature": "bad"})).status_code == 401

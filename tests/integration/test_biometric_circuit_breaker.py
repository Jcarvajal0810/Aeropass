"""US2 — an open circuit answers NO_CONCLUYENTE instantly (FR-009, SC-007)."""

import time

import pytest

from tests.integration.helpers import register, verify

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_breaker_opens_after_five_timeouts(client, container):
    assert (await register(client, "u1")).status_code == 201
    provider = container.biometric_provider.inner

    for _ in range(5):
        started = time.perf_counter()
        body = (await verify(client, "u1", "timeout")).json()
        assert body["resultado"] == "NO_CONCLUYENTE"
        assert time.perf_counter() - started < 10  # SC-007
    calls_before = provider.calls

    started = time.perf_counter()
    body = (await verify(client, "u1", "timeout")).json()
    elapsed = time.perf_counter() - started

    assert body["resultado"] == "NO_CONCLUYENTE"
    assert elapsed < 1
    assert provider.calls == calls_before  # the provider was not called
    me = await client.get("/v1/identity/me", headers={"Authorization": "Bearer test:u1"})
    assert me.json()["intentos_fallidos"] == 0

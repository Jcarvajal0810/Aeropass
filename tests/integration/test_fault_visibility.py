"""Spec 003 — injected faults must move the dashboard: a slow request is a visible step."""

from __future__ import annotations

import time

import httpx
import pytest

from aeropass.config import Settings
from aeropass.main import create_app
from tests.integration.helpers import verified_passenger

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


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


async def _client(container) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_slow_is_a_visible_step_of_the_request(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "slow-pass")
        started = time.perf_counter()
        response = await client.post(
            "/v1/passes",
            json={"codigo_vuelo": "AV9380"},
            headers={"Authorization": "Bearer test:slow-pass", "X-AeroPass-Fault": "slow:300"},
        )
        elapsed = time.perf_counter() - started

    assert response.status_code == 201
    assert response.headers["x-aeropass-fault-applied"] == "slow"
    assert elapsed >= 0.3
    [issue] = [t for t in sentry_capture.transactions if t["transaction"] == "/v1/passes"]
    steps = {s["description"] for s in issue["spans"] if s["op"] == "aeropass.step"}
    assert "fault.slow" in steps  # W9 row; the transaction's duration feeds W6
    assert issue.get("tags", {}).get("fault_injected") == "slow"

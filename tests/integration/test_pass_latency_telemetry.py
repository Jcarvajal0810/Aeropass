"""US6 — pass latency, health checks and contingency in Sentry (spec 002, FR-013, FR-014)."""

from __future__ import annotations

import httpx
import pytest

from aeropass.adapters.clock import FakeClock
from aeropass.adapters.resilience.circuit_breaker import CircuitBreaker, InMemoryBreakerStateStore
from aeropass.main import create_app
from aeropass.observability import telemetry_catalog as tc
from aeropass.services.health_service import HealthService
from tests.integration.helpers import issue_pass, verified_passenger


class _OkCheck:
    name = "database"

    async def check(self) -> None:
        return None


async def _client(container) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.usefixtures("db")
async def test_pass_issue_and_detail_are_transactions_named_by_route(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "latency-user")
        issued = await issue_pass(client, "latency-user")
        assert issued.status_code == 201, issued.text
        credencial_id = issued.json()["credencial_id"]
        detail = await client.get(
            f"/v1/passes/{credencial_id}", headers={"Authorization": "Bearer test:latency-user"}
        )
        assert detail.status_code == 200, detail.text

    names = {t["transaction"] for t in sentry_capture.transactions}
    assert {"/v1/passes", "/v1/passes/{credencial_id}"} <= names
    assert not any(credencial_id in name for name in names)


async def test_health_checks_produce_no_transaction(container, sentry_capture):
    container.health_service = HealthService([_OkCheck()])
    async with await _client(container) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert sentry_capture.transactions == []


async def test_a_circuit_opening_becomes_a_contingency_metric(sentry_capture):
    breaker = CircuitBreaker("biometric", InMemoryBreakerStateStore(), FakeClock())

    async def fail() -> None:
        raise ConnectionError("provider down")

    for _ in range(5):
        with pytest.raises(ConnectionError):
            await breaker.call(fail, timeout=1)

    [metric] = sentry_capture.metrics_named("aeropass.circuit_breaker.apertura")
    assert metric["value"] == 1.0
    assert metric["attributes"]["aeropass.dependencia"] == "biometric"
    [log] = sentry_capture.audit_logs(tc.CIRCUIT_OPENED)
    assert log["level"] == "warn"

"""GET /health (spec 002, contracts/health-endpoint.md, FR-013a). Written before the endpoint."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from aeropass.main import create_app
from aeropass.services.health_service import HealthService


class Check:
    def __init__(self, name: str, behaviour: str = "ok") -> None:
        self.name = name
        self._behaviour = behaviour

    async def check(self) -> None:
        if self._behaviour == "raise":
            raise ConnectionError(f"could not connect to {self.name}.internal:5432")
        if self._behaviour == "hang":
            await asyncio.sleep(10)


async def _get(container, *checks: Check, path: str = "/health") -> httpx.Response:
    container.health_service = HealthService(list(checks), timeout=0.05)
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_ok_when_every_check_passes(container):
    response = await _get(container, Check("database"), Check("redis"))

    assert response.status_code == 200
    assert response.json() == {"estado": "ok"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("behaviour", ["raise", "hang"])
async def test_unavailable_when_a_check_fails_or_hangs(container, behaviour):
    response = await _get(container, Check("database"), Check("redis", behaviour))

    assert response.status_code == 503
    assert response.json() == {"estado": "no_disponible"}
    assert response.headers["cache-control"] == "no-store"
    # No internal detail: neither which check failed nor its error.
    assert "redis" not in response.text
    assert "5432" not in response.text


async def test_needs_no_authentication_and_is_not_in_the_public_schema(container):
    ok = await _get(container, Check("database"))
    schema = await _get(container, Check("database"), path="/openapi.json")

    assert ok.status_code == 200  # no Authorization header was sent
    assert "/health" not in schema.json()["paths"]

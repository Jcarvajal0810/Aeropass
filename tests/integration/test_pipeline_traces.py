"""US2 — one span per instrumented pipeline step (spec 002, FR-002, research §7).

Timeouts and open circuits must be told apart by the span status alone (US2, scenario 2).
"""

from __future__ import annotations

import json

import httpx
import pytest

from aeropass.main import create_app
from tests.integration.helpers import (
    DOC_DEFAULTS,
    document_number_for,
    issue_pass,
    register,
    verified_passenger,
    verify,
)

pytestmark = pytest.mark.usefixtures("db")

PIPELINE_STEPS = {
    "registration.register",
    "facade.verify_and_create_identity",
    "verification.record_attempt",
    "identity.create_for_success",
    "biometrics.mock.evaluate",
    "circuit_breaker.biometric",
    "passes.issue",
}


async def _client(container) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _steps(capture) -> list[dict]:
    return [
        span
        for transaction in capture.transactions
        for span in transaction.get("spans", [])
        if span["op"] == "aeropass.step"
    ]


def _breaker_statuses(capture) -> list[str]:
    return [s["status"] for s in _steps(capture) if s["description"] == "circuit_breaker.biometric"]


async def test_the_full_flow_has_a_span_per_step(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "trace-user")
        issued = await issue_pass(client, "trace-user")
        assert issued.status_code == 201, issued.text

    steps = _steps(sentry_capture)
    assert {s["description"] for s in steps} >= PIPELINE_STEPS
    assert all(s["status"] == "ok" for s in steps if s["description"] in PIPELINE_STEPS)


async def test_steps_are_children_of_the_request_transaction(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "tree-user")

    [verification] = [
        t for t in sentry_capture.transactions if t["transaction"] == "/v1/biometrics/verifications"
    ]
    names = {s["description"] for s in verification["spans"] if s["op"] == "aeropass.step"}
    assert {"facade.verify_and_create_identity", "circuit_breaker.biometric"} <= names


async def test_a_provider_timeout_is_deadline_exceeded(container, sentry_capture):
    async with await _client(container) as client:
        await register(client, "slow-user", numero_documento=document_number_for("slow-user"))
        response = await verify(client, "slow-user", "timeout")
        assert response.json()["resultado"] == "NO_CONCLUYENTE"

    assert _breaker_statuses(sentry_capture) == ["deadline_exceeded"]
    evaluate = [s for s in _steps(sentry_capture) if s["description"] == "biometrics.mock.evaluate"]
    assert [s["status"] for s in evaluate] == ["cancelled"]


async def test_an_open_circuit_is_unavailable(container, sentry_capture):
    async with await _client(container) as client:
        await register(client, "open-user", numero_documento=document_number_for("open-user"))
        for _ in range(5):  # NO_CONCLUYENTE does not count as a failed attempt
            await verify(client, "open-user", "timeout")
        response = await verify(client, "open-user", "ok")
        assert response.json()["resultado"] == "NO_CONCLUYENTE"

    assert _breaker_statuses(sentry_capture) == ["deadline_exceeded"] * 5 + ["unavailable"]


async def test_sql_and_http_spans_carry_no_values(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "values-user")

    text = json.dumps(sentry_capture.transactions)
    assert document_number_for("values-user") not in text
    assert DOC_DEFAULTS["nombre_completo"] not in text
    assert "MOCK:" not in text
    db_spans = [
        s for t in sentry_capture.transactions for s in t.get("spans", []) if s["op"] == "db"
    ]
    assert db_spans, "SQLAlchemy spans are expected"
    for span in (s for t in sentry_capture.transactions for s in t.get("spans", [])):
        assert "?" not in (span.get("data") or {}).get("url", "")
        assert "http.query" not in (span.get("data") or {})

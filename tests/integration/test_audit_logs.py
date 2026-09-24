"""US3 — audit records as correlatable logs without sensitive data (spec 002, FR-003, FR-006)."""

from __future__ import annotations

import json

import httpx
import pytest

from aeropass.main import create_app
from aeropass.observability import telemetry_catalog as tc
from tests.integration.helpers import (
    DOC_DEFAULTS,
    document_number_for,
    issue_pass,
    verified_passenger,
)

pytestmark = pytest.mark.usefixtures("db")


async def _client(container) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _outcomes(capture, event: str) -> list[str]:
    return [log["attributes"]["aeropass.outcome"] for log in capture.audit_logs(event)]


async def test_a_successful_pass_issue_is_an_ok_record(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "audit-ok")
        response = await issue_pass(client, "audit-ok")
        assert response.status_code == 201, response.text

    assert _outcomes(sentry_capture, tc.CREDENTIAL_ISSUE) == ["ok"]


async def test_a_failed_pass_issue_carries_only_the_error_type(container, sentry_capture):
    async with await _client(container) as client:
        response = await issue_pass(client, "never-registered")
        assert response.status_code >= 400

    [log] = sentry_capture.audit_logs(tc.CREDENTIAL_ISSUE)
    attributes = log["attributes"]
    assert attributes["aeropass.outcome"] == "error"
    assert attributes["aeropass.error_type"] == "IdentidadNoActiva"
    assert log["body"] == tc.LOG_BODY  # a fixed body, never the exception message
    assert {k for k in attributes if k.startswith("aeropass.")} == {
        tc.EVENT_ATTRIBUTE,
        "aeropass.outcome",
        "aeropass.error_type",
    }


async def test_only_failed_authentications_are_logged(container, sentry_capture):
    async with await _client(container) as client:
        rejected = await client.post("/v1/passes", json={"codigo_vuelo": "AV9380"})
        assert rejected.status_code == 401
        await verified_passenger(client, "audit-auth")  # several authenticated requests

    assert _outcomes(sentry_capture, tc.AUTH_AUTHENTICATE) == ["error"]
    [log] = sentry_capture.audit_logs(tc.AUTH_AUTHENTICATE)
    assert log["attributes"]["aeropass.error_type"] == "NoAutenticado"


async def test_audit_logs_carry_no_sensitive_data(container, sentry_capture):
    async with await _client(container) as client:
        await verified_passenger(client, "audit-pii")
        issued = await issue_pass(client, "audit-pii")
        token = issued.json()["token"]

    logs = json.dumps(sentry_capture.logs)
    assert sentry_capture.audit_logs(), "audit logs are expected"
    assert document_number_for("audit-pii") not in logs
    assert DOC_DEFAULTS["nombre_completo"] not in logs
    assert "MOCK:" not in logs
    assert token not in logs
    assert token not in sentry_capture.dump()

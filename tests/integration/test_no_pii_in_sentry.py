"""No sensitive data in anything sent to Sentry (spec 002, SC-003, FR-006).

The same flow as ``test_no_pii_in_logs`` (spoof, provider timeout, outbox down, success) plus a
pass issue, with the in-memory transport: every envelope (errors, transactions, logs, metrics,
breadcrumbs) is checked, not only log messages.
"""

from __future__ import annotations

import json

import httpx
import pytest

from aeropass.main import create_app
from tests.integration.helpers import DOC_DEFAULTS, issue_pass, register, verify

pytestmark = pytest.mark.usefixtures("db")

USER = "pii-sentry-user"


async def test_no_envelope_carries_sensitive_data(container, sentry_capture):
    transport = httpx.ASGITransport(app=create_app(container), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await register(client, USER)).status_code == 201
        await verify(client, USER, "spoof")
        await verify(client, USER, "timeout")
        container.event_publisher.inner.down = True  # the post-commit publish fails and logs
        assert (await verify(client, USER, "ok")).json()["resultado"] == "EXITOSO"
        issued = await issue_pass(client, USER)
        assert issued.status_code == 201, issued.text
        token = issued.json()["token"]

    sent = sentry_capture.dump()
    assert sentry_capture.transactions and sentry_capture.audit_logs(), "telemetry expected"

    forbidden = {
        "document number": DOC_DEFAULTS["numero_documento"],
        "full name": DOC_DEFAULTS["nombre_completo"],
        "full name (escaped)": json.dumps(DOC_DEFAULTS["nombre_completo"])[1:-1],
        "image marker": "MOCK:",
        "pass token": token,
        "token signature": token.rsplit(".", 1)[-1],
        "auth header": "Bearer test:",
        "signing key": "PRIVATE KEY",
    }
    leaked = [label for label, value in forbidden.items() if value in sent]
    assert leaked == []


async def test_biometric_scores_never_leave_the_process(container, sentry_capture):
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await register(client, "scores-user", numero_documento="5550001111")
        body = (await verify(client, "scores-user", "spoof")).json()

    assert body["score_liveness"] is not None
    sent = sentry_capture.dump()
    # SQL spans may name the columns (values are placeholders); a score would travel as a key.
    assert '"score_liveness"' not in sent
    assert '"score_comparacion"' not in sent
    for item in sentry_capture.logs + sentry_capture.metrics:
        assert not any("score" in key for key in item["attributes"])

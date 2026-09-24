"""US4/US5 — self-service and auto-rejection metrics (spec 002, FR-011, FR-012, KR A1.2).

Every passenger reaches a final state once, and the records are emitted after the commit.
"""

from __future__ import annotations

import httpx
import pytest

from aeropass.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from aeropass.main import create_app
from aeropass.services.identity_verification_facade import IdentityVerificationFacade
from tests.integration.helpers import document_number_for, register, verify

pytestmark = pytest.mark.usefixtures("db")

ESTADO_FINAL = "aeropass.pasajero.estado_final"


async def _client(container) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(container), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _passenger(client: httpx.AsyncClient, user_id: str, *markers: str) -> list[dict]:
    registered = await register(client, user_id, numero_documento=document_number_for(user_id))
    assert registered.status_code == 201, registered.text
    return [(await verify(client, user_id, marker)).json() for marker in markers]


def _final_states(capture) -> list[str]:
    return [m["attributes"]["aeropass.estado"] for m in capture.metrics_named(ESTADO_FINAL)]


# --- US4: self-service ----------------------------------------------------------------------
async def test_a_verified_passenger_counts_once(container, sentry_capture):
    async with await _client(container) as client:
        [result] = await _passenger(client, "self-ok", "ok")
        assert result["resultado"] == "EXITOSO"

    assert _final_states(sentry_capture) == ["VERIFICADO"]
    [metric] = sentry_capture.metrics_named(ESTADO_FINAL)
    assert metric["type"] == "counter"
    assert metric["value"] == 1.0


async def test_manual_review_counts_once_on_the_third_failure(container, sentry_capture):
    async with await _client(container) as client:
        results = await _passenger(client, "self-manual", "spoof", "other", "spoof")
        assert [r["resultado"] for r in results] == ["FALLIDO"] * 3

    assert _final_states(sentry_capture) == ["REQUIERE_REVISION_MANUAL"]


async def test_an_inconclusive_attempt_does_not_count(container, sentry_capture):
    async with await _client(container) as client:
        [result] = await _passenger(client, "self-timeout", "timeout")
        assert result["resultado"] == "NO_CONCLUYENTE"

    assert _final_states(sentry_capture) == []


async def test_nothing_is_emitted_when_the_commit_fails(container, sentry_capture):
    class FailingCommit(SqlAlchemyUnitOfWork):
        async def commit(self) -> None:
            raise ConnectionError("commit lost")

    async with await _client(container) as client:
        registered = await register(
            client, "self-rollback", numero_documento=document_number_for("self-rollback")
        )
        assert registered.status_code == 201, registered.text
        container.facade = IdentityVerificationFacade(
            lambda: FailingCommit(container.session_factory),
            container.media_storage,
            container.biometric_provider,
            container.verification_service,
            container.identity_service,
            container.outbox_dispatcher,
        )
        response = await verify(client, "self-rollback", "ok")
        assert response.status_code == 500

    assert sentry_capture.metrics == []
    [log] = sentry_capture.audit_logs("identity.verification")
    assert log["attributes"]["aeropass.outcome"] == "error"
    assert "aeropass.estado" not in log["attributes"]

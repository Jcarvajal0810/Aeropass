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


# --- US5: auto-rejection --------------------------------------------------------------------
INTENTO = "aeropass.verificacion.intento"


def _attempts(capture) -> list[tuple[str, str | None]]:
    return [
        (m["attributes"]["aeropass.resultado"], m["attributes"].get("aeropass.motivo"))
        for m in capture.metrics_named(INTENTO)
    ]


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("ok", ("EXITOSO", None)),
        ("spoof", ("FALLIDO", "LIVENESS")),
        ("other", ("FALLIDO", "COMPARACION")),
        ("timeout", ("NO_CONCLUYENTE", None)),
    ],
)
async def test_each_attempt_counts_with_its_result_and_reason(
    container, sentry_capture, marker, expected
):
    async with await _client(container) as client:
        await _passenger(client, f"attempt-{marker}", marker)

    assert _attempts(sentry_capture) == [expected]


async def test_a_provider_quota_error_is_inconclusive_never_a_rejection(container, sentry_capture):
    """F18 hypothesis (research §13): a 429 must not reject a legitimate passenger."""
    from aeropass.adapters.biometrics.factory import ResilientBiometricProvider
    from aeropass.adapters.biometrics.vision_adapter import VisionProviderAdapter

    quota = httpx.MockTransport(lambda request: httpx.Response(429, json={"error": "quota"}))
    container.biometric_provider = ResilientBiometricProvider(
        VisionProviderAdapter(
            httpx.AsyncClient(transport=quota), "https://vision.test/v1/verify", "secret-key"
        ),
        container.breaker("biometric"),
        timeout=1,
    )

    async with await _client(container) as client:
        [result] = await _passenger(client, "attempt-quota", "ok")

    assert result["resultado"] == "NO_CONCLUYENTE"
    assert _attempts(sentry_capture) == [("NO_CONCLUYENTE", None)]
    http_spans = [
        s
        for t in sentry_capture.transactions
        for s in t.get("spans", [])
        if s["op"] == "http.client"
    ]
    assert [s["data"].get("http.response.status_code") for s in http_spans] == [429]
    assert "secret-key" not in sentry_capture.dump()


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

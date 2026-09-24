"""Sentry initialisation (spec 002, research §2–§4, FR-004, FR-009). Written before the setup."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import sentry_sdk

from aeropass.adapters.observability.sentry_privacy import SentryPrivacyFilter
from aeropass.adapters.observability.sentry_setup import (
    configure_observability,
    reset_observability,
)
from aeropass.api.deps import Container
from aeropass.main import create_app
from aeropass.observability import hooks
from tests.support.sentry_capture import CapturingTransport, sentry_settings


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_observability()
    yield
    reset_observability()


def test_without_dsn_nothing_is_initialised():
    assert configure_observability(sentry_settings(sentry_dsn="")) is False

    assert not sentry_sdk.get_client().is_active()
    assert hooks._span_hooks == []
    assert hooks._audit_hooks == []


async def test_without_dsn_the_app_answers_as_before():
    app = create_app(Container(sentry_settings(sentry_dsn="")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        response = await c.get("/openapi.json")

    assert response.status_code == 200
    assert not sentry_sdk.get_client().is_active()


def test_with_dsn_the_sdk_runs_with_the_privacy_options():
    assert configure_observability(sentry_settings(), transport=CapturingTransport()) is True

    options = sentry_sdk.get_client().options
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["enable_logs"] is True
    assert options["environment"] == "test"
    assert options["release"] == "test-release"
    for hook in (
        "before_send",
        "before_send_transaction",
        "before_breadcrumb",
        "before_send_log",
        "before_send_metric",
    ):
        assert isinstance(options[hook].__self__, SentryPrivacyFilter), hook


def test_only_the_needed_integrations_load_to_keep_cold_starts_short():
    """Auto-enabling probes ~50 libraries on every cold start (spec 002, SC-004, T055)."""
    configure_observability(sentry_settings(), transport=CapturingTransport())

    options = sentry_sdk.get_client().options
    assert options["auto_enabling_integrations"] is False
    installed = set(sentry_sdk.get_client().integrations)
    assert {"starlette", "fastapi", "logging", "sqlalchemy", "httpx"} <= installed


def test_initialisation_is_idempotent_and_hooks_register_once():
    settings = sentry_settings()
    configure_observability(settings, transport=CapturingTransport())
    configure_observability(settings, transport=CapturingTransport())
    create_app(Container(settings))
    create_app(Container(settings))

    assert len(hooks._span_hooks) == 1
    assert len(hooks._audit_hooks) == 1


@pytest.fixture
def scheduled(monkeypatch) -> list[object]:
    from aeropass.adapters.observability import sentry_setup

    calls: list[object] = []

    def fake_wait_until(awaitable: object) -> None:
        calls.append(awaitable)
        awaitable.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(sentry_setup, "wait_until", fake_wait_until)
    return calls


async def _call(app, scope_type: str = "http") -> None:
    from aeropass.adapters.observability.sentry_setup import FlushTelemetryMiddleware

    async def receive() -> dict:
        return {"type": "http.request"}

    async def send(message: dict) -> None:
        return None

    await FlushTelemetryMiddleware(app)({"type": scope_type}, receive, send)


async def test_flush_is_scheduled_after_each_http_response(scheduled):
    configure_observability(sentry_settings(), transport=CapturingTransport())

    async def app(scope, receive, send) -> None:
        return None

    await _call(app)
    await _call(app, "lifespan")

    assert len(scheduled) == 1


async def test_flush_is_scheduled_even_when_the_app_raises(scheduled):
    configure_observability(sentry_settings(), transport=CapturingTransport())

    async def app(scope, receive, send) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _call(app)

    assert len(scheduled) == 1


async def test_no_flush_without_an_active_client(scheduled):
    async def app(scope, receive, send) -> None:
        return None

    await _call(app)

    assert scheduled == []


def test_health_checks_are_never_traced_and_the_rest_use_the_rate():
    configure_observability(
        sentry_settings(sentry_traces_sample_rate=0.2), transport=CapturingTransport()
    )
    sampler = sentry_sdk.get_client().options["traces_sampler"]

    assert sampler({"asgi_scope": {"type": "http", "path": "/health"}}) == 0
    assert sampler({"asgi_scope": {"type": "http", "path": "/v1/passes"}}) == 0.2
    assert sampler({}) == 0.2

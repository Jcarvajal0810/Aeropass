"""Spec 003 — fault plan parsing, tripping, wrappers, middleware and the production guard."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from aeropass.adapters.faults import context
from aeropass.adapters.faults.context import (
    BLOB_DOWN,
    DEFAULT_SLOW_MS,
    MAX_SLOW_MS,
    MXFACE_SLOW,
    REDIS_DOWN,
    activate,
    current,
    deactivate,
    parse,
    trip,
)
from aeropass.adapters.faults.wrappers import FaultProxy
from aeropass.api.fault_injection import FaultInjectionMiddleware
from aeropass.config import Settings
from aeropass.observability import hooks
from aeropass.observability.telemetry_catalog import FAULT_INJECTED


# --- parsing ----------------------------------------------------------------------------
def test_parse_several_faults_and_the_slow_value():
    plan, unknown = parse(" blob_down , MXFACE_SLOW:8000,redis_down")
    assert plan.faults == {BLOB_DOWN, MXFACE_SLOW, REDIS_DOWN}
    assert plan.slow_ms == 8000
    assert unknown == []


def test_parse_reports_unknown_and_bad_values():
    plan, unknown = parse("teleport,mxface_slow:abc,,blob_down")
    assert plan.faults == {BLOB_DOWN}
    assert unknown == ["teleport", "mxface_slow:abc"]


def test_slow_defaults_and_is_capped():
    assert parse("mxface_slow")[0].slow_ms == DEFAULT_SLOW_MS
    assert parse("mxface_slow:999999")[0].slow_ms == MAX_SLOW_MS


# --- tripping ---------------------------------------------------------------------------
@pytest.fixture
def audits() -> Any:
    events: list[tuple[str, dict[str, Any]]] = []

    def hook(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    hooks.register_audit_hook(hook)
    yield events
    hooks._audit_hooks.remove(hook)


def test_trip_is_false_without_a_plan_or_the_fault(audits):
    assert trip(BLOB_DOWN) is False
    token = activate(parse("redis_down")[0])
    try:
        assert trip(BLOB_DOWN) is False
    finally:
        deactivate(token)
    assert audits == []


def test_trip_records_each_fault_once_per_request(audits):
    plan = parse("blob_down")[0]
    token = activate(plan)
    try:
        assert trip(BLOB_DOWN) is True
        assert trip(BLOB_DOWN) is True
    finally:
        deactivate(token)
    assert plan.applied == {BLOB_DOWN}
    assert audits == [(FAULT_INJECTED, {"fault": BLOB_DOWN})]
    assert current() is None


# --- wrappers ---------------------------------------------------------------------------
class _Client:
    region = "gru1"

    def ping(self) -> str:
        return "pong"


def test_fault_proxy_delegates_until_its_fault_is_asked():
    proxy = FaultProxy(_Client(), REDIS_DOWN, lambda: ConnectionError("down"))
    assert proxy.ping() == "pong"
    assert proxy.region == "gru1"
    token = activate(parse("redis_down")[0])
    try:
        with pytest.raises(ConnectionError):
            proxy.ping()
        assert proxy.region == "gru1"  # plain attributes never fail
    finally:
        deactivate(token)


# --- middleware -------------------------------------------------------------------------
async def _call(
    middleware_secret: str, headers: list[tuple[bytes, bytes]]
) -> tuple[Any, list[dict[str, Any]]]:
    seen: dict[str, Any] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        plan = current()
        seen["plan"] = plan
        if plan is not None:
            trip(BLOB_DOWN)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = FaultInjectionMiddleware(app, secret=middleware_secret)
    await middleware({"type": "http", "headers": headers}, None, send)
    return seen["plan"], sent


async def test_middleware_activates_the_plan_and_reports_what_applied():
    plan, sent = await _call("", [(b"x-aeropass-fault", b"blob_down")])
    assert plan is not None and plan.faults == {BLOB_DOWN}
    assert (b"x-aeropass-fault-applied", b"blob_down") in sent[0]["headers"]
    assert current() is None  # deactivated after the request


async def test_middleware_without_header_does_nothing():
    plan, sent = await _call("", [])
    assert plan is None
    assert sent[0]["headers"] == []


async def test_middleware_requires_the_key_when_a_secret_is_set():
    plan, _ = await _call("s3cret", [(b"x-aeropass-fault", b"blob_down")])
    assert plan is None
    plan, _ = await _call(
        "s3cret",
        [(b"x-aeropass-fault", b"blob_down"), (b"x-aeropass-fault-key", b"wrong")],
    )
    assert plan is None
    plan, _ = await _call(
        "s3cret",
        [(b"x-aeropass-fault", b"blob_down"), (b"x-aeropass-fault-key", b"s3cret")],
    )
    assert plan is not None


# --- production guard -------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [{"vercel_env": "production"}, {"sentry_environment": "prod"}],
)
def test_fault_injection_is_refused_in_production(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, fault_injection_enabled=True, **overrides)  # type: ignore[call-arg]


def test_fault_injection_is_allowed_on_a_preview():
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        fault_injection_enabled=True,
        vercel_env="preview",
        sentry_environment="chaos",
    )
    assert settings.fault_injection_enabled


def test_context_module_exposes_every_fault_the_app_offers():
    # The app's chaos panel (AeroPass-App, lib/core/fault_injection.dart) offers these.
    offered = {
        "blob_down",
        "mxface_down",
        "mxface_slow",
        "mxface_quota",
        "db_down",
        "redis_down",
        "signing_down",
        "qstash_down",
    }
    assert offered <= context.KNOWN_FAULTS

"""Sentry initialisation for the serverless backend (spec 002, research §2–§4, §8).

``configure_observability`` runs once per process, before the FastAPI app is built, and does
nothing without a DSN (FR-004, FR-009). ``FlushTelemetryMiddleware`` wraps the ASGI entrypoint so
queued telemetry is sent after the response, without the passenger waiting (FR-007).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.utils import capture_internal_exceptions
from vercel.functions import wait_until

from aeropass.adapters.observability.sentry_privacy import SentryPrivacyFilter
from aeropass.adapters.observability.sentry_sinks import SentryAuditSink, SentrySpanHook
from aeropass.config import Settings
from aeropass.observability import hooks

HEALTH_PATH = "/health"
FLUSH_TIMEOUT_SECONDS = 2.0
_SERVER_ERRORS = set(range(500, 600))

_configured = False


def traces_sampler_for(rate: float) -> Any:
    def sampler(context: dict[str, Any]) -> float:
        asgi_scope = context.get("asgi_scope") or {}
        if asgi_scope.get("path") == HEALTH_PATH:  # 1,440 checks a day: cost with no value
            return 0.0
        return rate

    return sampler


def configure_observability(settings: Settings, *, transport: Transport | None = None) -> bool:
    """Initialise Sentry and register the hook sinks. Returns whether observability is on."""
    global _configured
    if not settings.sentry_dsn:
        return False
    if _configured:
        return True

    privacy = SentryPrivacyFilter()
    options: dict[str, Any] = {
        "dsn": settings.sentry_dsn,
        "environment": settings.sentry_environment,
        "release": settings.vercel_git_commit_sha or None,
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "enable_logs": True,
        "traces_sampler": traces_sampler_for(settings.sentry_traces_sample_rate),
        "before_send": privacy.before_send,
        "before_send_transaction": privacy.before_send_transaction,
        "before_breadcrumb": privacy.before_breadcrumb,
        "before_send_log": privacy.before_send_log,
        "before_send_metric": privacy.before_send_metric,
        "integrations": [
            StarletteIntegration(
                transaction_style="url", failed_request_status_codes=_SERVER_ERRORS
            ),
            FastApiIntegration(transaction_style="url", failed_request_status_codes=_SERVER_ERRORS),
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
                sentry_logs_level=logging.WARNING,
            ),
        ],
    }
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(**options)

    hooks.register_span_hook(SentrySpanHook())
    hooks.register_audit_hook(SentryAuditSink())
    _configured = True
    return True


def reset_observability() -> None:
    """Close the client and unregister the sinks (tests only)."""
    global _configured
    client = sentry_sdk.get_client()
    if client.is_active():
        client.close(timeout=0)
    sentry_sdk.get_global_scope().set_client(None)
    hooks.clear_hooks()
    _configured = False


def flush_telemetry(timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
    """Send what is queued now (short-lived processes such as tools)."""
    if sentry_sdk.get_client().is_active():
        sentry_sdk.flush(timeout)


def schedule_flush() -> None:
    """Send what is queued after the response; a no-op outside a Vercel invocation."""
    if not sentry_sdk.get_client().is_active():
        return
    with capture_internal_exceptions():
        wait_until(asyncio.to_thread(sentry_sdk.flush, FLUSH_TIMEOUT_SECONDS))


class FlushTelemetryMiddleware:
    """Outermost ASGI wrapper (``api/index.py``).

    It has to sit outside the app: Sentry captures unhandled errors in its own ASGI wrapper, after
    every middleware of the app has returned, and the flush must see those events.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        try:
            await self.app(scope, receive, send)
        finally:
            if scope.get("type") == "http":
                schedule_flush()

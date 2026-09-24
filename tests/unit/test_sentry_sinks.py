"""Sentry span hook and audit sink (spec 002, research §5–§7). Written before the sinks."""

from __future__ import annotations

import asyncio

import pytest
import sentry_sdk

from aeropass.adapters.observability.sentry_sinks import SentrySpanHook
from aeropass.adapters.resilience.circuit_breaker import CircuitOpenError
from aeropass.observability import hooks
from aeropass.observability import telemetry_catalog as tc


def _step_spans(capture) -> list[dict]:
    return [
        s for t in capture.transactions for s in t.get("spans", []) if s["op"] == "aeropass.step"
    ]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (None, "ok"),
        (TimeoutError(), "deadline_exceeded"),
        (CircuitOpenError("biometric"), "unavailable"),
        (asyncio.CancelledError(), "cancelled"),
        (ValueError("x"), "internal_error"),
    ],
)
def test_span_status_follows_the_exception(sentry_capture, error, status):
    with sentry_sdk.start_transaction(name="test", op="test"):
        try:
            with SentrySpanHook()("circuit_breaker.biometric"):
                if error is not None:
                    raise error
        except BaseException as exc:
            assert exc is error  # re-raised unchanged

    [span] = _step_spans(sentry_capture)
    assert span["description"] == "circuit_breaker.biometric"
    assert span["status"] == status


def test_traced_steps_become_spans(sentry_capture):
    @hooks.traced("passes.issue")
    def issue() -> int:
        return 1

    with sentry_sdk.start_transaction(name="test", op="test"):
        issue()

    assert [s["description"] for s in _step_spans(sentry_capture)] == ["passes.issue"]


def test_each_audit_record_becomes_one_log(sentry_capture):
    hooks.emit_audit(tc.CREDENTIAL_ISSUE, outcome="error", error="IdentidadNoActiva")

    [log] = sentry_capture.audit_logs(tc.CREDENTIAL_ISSUE)
    assert log["body"] == tc.LOG_BODY
    assert log["level"] == "info"
    assert log["attributes"][tc.EVENT_ATTRIBUTE] == tc.CREDENTIAL_ISSUE
    assert log["attributes"]["aeropass.outcome"] == "error"
    assert log["attributes"]["aeropass.error_type"] == "IdentidadNoActiva"


def test_circuit_openings_are_logged_as_warnings(sentry_capture):
    hooks.emit_audit(tc.CIRCUIT_OPENED, dependencia="biometric")

    [log] = sentry_capture.audit_logs(tc.CIRCUIT_OPENED)
    assert log["level"] == "warn"
    assert log["attributes"]["aeropass.dependencia"] == "biometric"


def test_successful_authentication_is_not_logged(sentry_capture):
    hooks.emit_audit(tc.AUTH_AUTHENTICATE, outcome="ok")
    hooks.emit_audit(tc.AUTH_AUTHENTICATE, outcome="error", error="NoAutenticado")

    logs = sentry_capture.audit_logs(tc.AUTH_AUTHENTICATE)
    assert [log["attributes"]["aeropass.outcome"] for log in logs] == ["error"]


def test_events_outside_the_catalog_are_ignored(sentry_capture):
    hooks.emit_audit("something.else", outcome="ok")

    assert sentry_capture.audit_logs() == []


def test_metric_rules_turn_audit_records_into_counters(sentry_capture, monkeypatch):
    rule = tc.MetricRule(
        "aeropass.test.estado_final",
        tc.IDENTITY_VERIFICATION,
        frozenset({"estado"}),
        when=lambda data: data.get("estado") is not None,
    )
    monkeypatch.setattr(tc, "METRIC_RULES", (rule,))

    hooks.emit_audit(
        tc.IDENTITY_VERIFICATION, outcome="ok", resultado="EXITOSO", estado="VERIFICADO"
    )
    hooks.emit_audit(tc.IDENTITY_VERIFICATION, outcome="ok", resultado="FALLIDO")

    [metric] = sentry_capture.metrics_named("aeropass.test.estado_final")
    assert metric["type"] == "counter"
    assert metric["value"] == 1.0
    assert metric["attributes"]["aeropass.estado"] == "VERIFICADO"
    assert "aeropass.resultado" not in metric["attributes"]

"""SentryPrivacyFilter (spec 002, contracts/privacy-filter.md, FR-006).

Written before the filter (test-first on the privacy boundary).
"""

from __future__ import annotations

from typing import Any

import pytest

from aeropass.adapters.observability.sentry_privacy import REDACTED, SentryPrivacyFilter
from aeropass.observability import telemetry_catalog as tc


@pytest.fixture
def f() -> SentryPrivacyFilter:
    return SentryPrivacyFilter()


def _error_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "level": "error",
        "user": {"id": "user_123", "ip_address": "181.1.2.3"},
        "request": {
            "method": "POST",
            "url": "https://aeropass.test/v1/identity?token=secret",
            "query_string": "token=secret",
            "data": {"numero_documento": "1234567890", "nombre_completo": "Ana Pérez"},
            "cookies": {"session": "abc"},
            "headers": {
                "Authorization": "Bearer eyJ.secret",
                "Content-Type": "application/json",
                "User-Agent": "okhttp/4",
                "X-Forwarded-For": "181.1.2.3",
            },
            "env": {"REMOTE_ADDR": "181.1.2.3"},
        },
        "exception": {"values": []},
    }
    event.update(overrides)
    return event


# --- before_send ----------------------------------------------------------------------------
def test_request_body_cookies_query_and_user_are_removed(f):
    out = f.before_send(_error_event(), {})

    assert "user" not in out
    request = out["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "query_string" not in request
    assert "env" not in request
    assert request["url"] == "https://aeropass.test/v1/identity"


def test_only_content_type_and_user_agent_headers_survive(f):
    headers = f.before_send(_error_event(), {})["request"]["headers"]

    assert {k.lower() for k in headers} == {"content-type", "user-agent"}
    assert "Bearer eyJ.secret" not in str(headers)


def test_third_party_exception_message_is_redacted_but_type_and_stack_kept(f):
    value = "duplicate key value violates unique constraint\nDETAIL: Key (numero)=(1234567890)"
    frames = [{"function": "execute", "module": "asyncpg.connection"}]
    event = _error_event(
        exception={
            "values": [
                {
                    "type": "UniqueViolationError",
                    "module": "asyncpg.exceptions",
                    "value": value,
                    "stacktrace": {"frames": frames},
                }
            ]
        }
    )

    exc = f.before_send(event, {})["exception"]["values"][0]

    assert exc["value"] == REDACTED
    assert exc["type"] == "UniqueViolationError"
    assert exc["stacktrace"]["frames"] == frames


def test_builtin_exception_message_is_redacted(f):
    event = _error_event(
        exception={"values": [{"type": "KeyError", "module": None, "value": "'1234567890'"}]}
    )

    assert f.before_send(event, {})["exception"]["values"][0]["value"] == REDACTED


def test_domain_error_keeps_its_fixed_message(f):
    event = _error_event(
        exception={
            "values": [
                {
                    "type": "DatosInvalidos",
                    "module": "aeropass.domain.errors",
                    "value": "Invalid data",
                }
            ]
        }
    )

    assert f.before_send(event, {})["exception"]["values"][0]["value"] == "Invalid data"


def test_third_party_logger_message_is_redacted(f):
    event = {
        "level": "error",
        "logger": "sqlalchemy.engine",
        "logentry": {"message": "failed for %s", "params": ["1234567890"], "formatted": "x"},
    }

    out = f.before_send(event, {})

    assert out["logentry"] == {"message": REDACTED}


def test_aeropass_logger_message_is_kept(f):
    logentry = {"message": "event %s not delivered: %s", "params": ["uuid", "PublishFailed"]}
    event = {
        "level": "error",
        "logger": "aeropass.services.outbox_dispatcher",
        "logentry": logentry,
    }

    assert f.before_send(event, {})["logentry"] == logentry


def test_before_send_never_drops_an_error(f):
    assert f.before_send({"level": "error"}, {}) is not None
    assert f.before_send(_error_event(), {}) is not None


# --- before_send_transaction ----------------------------------------------------------------
def test_transaction_request_and_span_queries_are_scrubbed(f):
    transaction = {
        "type": "transaction",
        "user": {"ip_address": "181.1.2.3"},
        "request": _error_event()["request"],
        "spans": [
            {
                "op": "http.client",
                "description": "GET https://blob.test/selfies/x?token=secret",
                "data": {
                    "url": "https://blob.test/selfies/x?token=secret",
                    "http.query": "token=secret",
                    "http.fragment": "frag",
                    "http.method": "GET",
                },
            },
            {"op": "db", "description": "SELECT 1 WHERE numero = $1", "data": {}},
        ],
    }

    out = f.before_send_transaction(transaction, {})

    assert "user" not in out
    assert "data" not in out["request"]
    http_span = out["spans"][0]
    assert http_span["data"]["url"] == "https://blob.test/selfies/x"
    assert "http.query" not in http_span["data"]
    assert "http.fragment" not in http_span["data"]
    assert http_span["description"] == "GET https://blob.test/selfies/x"
    assert "secret" not in str(out)
    assert out["spans"][1]["description"] == "SELECT 1 WHERE numero = $1"


# --- before_breadcrumb ----------------------------------------------------------------------
def test_http_breadcrumb_keeps_method_status_and_url_without_query(f):
    crumb = {
        "type": "http",
        "category": "httplib",
        "data": {
            "url": "https://vision.test/v1?api_key=secret",
            "http.query": "api_key=secret",
            "method": "POST",
            "status_code": 429,
            "reason": "Too Many Requests",
        },
    }

    out = f.before_breadcrumb(crumb, {})

    assert out["data"] == {"url": "https://vision.test/v1", "method": "POST", "status_code": 429}


def test_sql_and_aeropass_log_breadcrumbs_are_kept(f):
    sql = {"category": "query", "message": "SELECT 1"}
    log = {"category": "aeropass.adapters.resilience.circuit_breaker", "message": "opened"}

    assert f.before_breadcrumb(sql, {}) == sql
    assert f.before_breadcrumb(log, {}) == log


def test_other_breadcrumbs_are_dropped(f):
    assert f.before_breadcrumb({"category": "httpx", "message": "HTTP Request: GET"}, {}) is None
    assert f.before_breadcrumb({"category": "console", "message": "x"}, {}) is None


# --- before_send_log ------------------------------------------------------------------------
def _log(attributes: dict[str, Any], body: str = tc.LOG_BODY) -> dict[str, Any]:
    return {"body": body, "severity_text": "info", "attributes": attributes}


def test_audit_log_keeps_allowlisted_and_sdk_attributes_only(f):
    log = _log(
        {
            tc.EVENT_ATTRIBUTE: tc.IDENTITY_VERIFICATION,
            "aeropass.outcome": "ok",
            "aeropass.resultado": "EXITOSO",
            "aeropass.numero_documento": "1234567890",
            "sentry.environment": "test",
            "server.address": "host",
            "user.id": "user_123",
        }
    )

    attributes = f.before_send_log(log, {})["attributes"]

    assert attributes == {
        tc.EVENT_ATTRIBUTE: tc.IDENTITY_VERIFICATION,
        "aeropass.outcome": "ok",
        "aeropass.resultado": "EXITOSO",
        "sentry.environment": "test",
        "server.address": "host",
    }


def test_aeropass_logger_log_passes_and_third_party_logger_log_is_dropped(f):
    ours = _log({"logger.name": "aeropass.health", "code.line.number": 3}, body="check failed")
    httpx = _log({"logger.name": "httpx"}, body="HTTP Request: GET https://x?token=1")

    assert f.before_send_log(ours, {}) is not None
    assert f.before_send_log(httpx, {}) is None


def test_log_without_origin_is_dropped(f):
    assert f.before_send_log(_log({"sentry.environment": "test"}, body="whatever"), {}) is None


# --- before_send_metric ---------------------------------------------------------------------
def test_metric_outside_catalog_is_dropped(f):
    metric = {"name": "aeropass.unknown", "type": "counter", "value": 1.0, "attributes": {}}

    assert f.before_send_metric(metric, {}) is None


def test_catalog_metric_keeps_only_its_attributes(f, monkeypatch):
    rule = tc.MetricRule("aeropass.test.metric", tc.IDENTITY_VERIFICATION, frozenset({"estado"}))
    monkeypatch.setattr(tc, "METRIC_RULES", (rule,))
    metric = {
        "name": "aeropass.test.metric",
        "type": "counter",
        "value": 1.0,
        "attributes": {
            "aeropass.estado": "VERIFICADO",
            "aeropass.resultado": "EXITOSO",
            "sentry.environment": "test",
            "user.id": "u",
        },
    }

    attributes = f.before_send_metric(metric, {})["attributes"]

    assert attributes == {"aeropass.estado": "VERIFICADO", "sentry.environment": "test"}


def test_outgoing_http_spans_name_their_host_for_w10(f):
    # sentry-sdk's httpx integration records the URL but not server.address (spec 003).
    event = {
        "spans": [
            {
                "op": "http.client",
                "data": {"url": "https://faceapi.mxface.ai/api/v3/face/verify?k=1"},
            },
            {"op": "http.client", "data": {"url": "https://x.io/a", "server.address": "kept.io"}},
            {"op": "aeropass.step", "data": {}},
        ]
    }
    spans = f.before_send_transaction(event, {})["spans"]
    assert spans[0]["data"]["server.address"] == "faceapi.mxface.ai"
    assert spans[0]["data"]["url"] == "https://faceapi.mxface.ai/api/v3/face/verify"
    assert spans[1]["data"]["server.address"] == "kept.io"
    assert "server.address" not in spans[2]["data"]

"""Single privacy filter for everything the Sentry SDK sends (spec 002, FR-006).

Contract: specs/002-observabilidad-sentry/contracts/privacy-filter.md. It cleans instead of
dropping errors, and works as an allowlist for logs, metrics and breadcrumbs, so a field nobody
reviewed never leaves the process.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aeropass.observability import telemetry_catalog as tc

REDACTED = "[redactado]"

_OWN_PREFIX = "aeropass."
_KEPT_HEADERS = frozenset({"content-type", "user-agent"})
_REQUEST_FIELDS_DROPPED = ("data", "cookies", "query_string", "env")
_SPAN_DATA_DROPPED = ("http.query", "http.fragment")
_HTTP_CRUMB_DATA_KEPT = ("method", "status_code", "url")
# Attributes the SDK adds itself (environment, release, SDK, logger and code location).
_SDK_LOG_PREFIXES = ("sentry.", "server.", "code.", "logger.")
_SDK_METRIC_PREFIXES = ("sentry.", "server.")

Event = dict[str, Any]


def strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _is_own(name: object) -> bool:
    return isinstance(name, str) and name.startswith(_OWN_PREFIX)


def _clean_request(event: Event) -> None:
    event.pop("user", None)
    request = event.get("request")
    if not isinstance(request, dict):
        return
    for key in _REQUEST_FIELDS_DROPPED:
        request.pop(key, None)
    if isinstance(request.get("url"), str):
        request["url"] = strip_query(request["url"])
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {k: v for k, v in headers.items() if k.lower() in _KEPT_HEADERS}


def _clean_exceptions(event: Event) -> None:
    for value in (event.get("exception") or {}).get("values") or []:
        # Our own exceptions carry fixed English messages; any other may echo data.
        if value.get("value") and not _is_own(value.get("module")):
            value["value"] = REDACTED


def _clean_logentry(event: Event) -> None:
    logger_name = event.get("logger")
    if "logentry" in event and logger_name is not None and not _is_own(logger_name):
        event["logentry"] = {"message": REDACTED}


def _clean_span(span: dict[str, Any]) -> None:
    data = span.get("data")
    if isinstance(data, dict):
        for key in _SPAN_DATA_DROPPED:
            data.pop(key, None)
        if isinstance(data.get("url"), str):
            data["url"] = strip_query(data["url"])
    description = span.get("description")
    if isinstance(description, str) and "?" in description:
        span["description"] = description.split("?", 1)[0]


def _keep(attributes: dict[str, Any], allowed: frozenset[str], prefixes: tuple[str, ...]) -> dict:
    return {k: v for k, v in attributes.items() if k in allowed or k.startswith(prefixes)}


class SentryPrivacyFilter:
    def before_send(self, event: Event, hint: dict[str, Any]) -> Event:
        _clean_request(event)
        _clean_exceptions(event)
        _clean_logentry(event)
        return event

    def before_send_transaction(self, event: Event, hint: dict[str, Any]) -> Event:
        _clean_request(event)
        for span in event.get("spans") or []:
            _clean_span(span)
        return event

    def before_breadcrumb(self, crumb: Event, hint: dict[str, Any]) -> Event | None:
        category = crumb.get("category")
        if crumb.get("type") == "http" or category in ("http", "httplib"):
            data = crumb.get("data") or {}
            kept = {k: data[k] for k in _HTTP_CRUMB_DATA_KEPT if k in data}
            if isinstance(kept.get("url"), str):
                kept["url"] = strip_query(kept["url"])
            crumb["data"] = kept
            return crumb
        if category == "query" or _is_own(category):
            return crumb
        return None

    def before_send_log(self, log: Event, hint: dict[str, Any]) -> Event | None:
        attributes = log.get("attributes") or {}
        is_audit = tc.EVENT_ATTRIBUTE in attributes
        if not is_audit and not _is_own(attributes.get("logger.name")):
            return None
        log["attributes"] = _keep(attributes, tc.LOG_ATTRIBUTES, _SDK_LOG_PREFIXES)
        return log

    def before_send_metric(self, metric: Event, hint: dict[str, Any]) -> Event | None:
        allowed = tc.metric_attributes().get(metric.get("name", ""))
        if allowed is None:
            return None
        metric["attributes"] = _keep(metric.get("attributes") or {}, allowed, _SDK_METRIC_PREFIXES)
        return metric

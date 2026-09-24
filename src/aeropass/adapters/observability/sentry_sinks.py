"""Sentry implementations of the Principle VI hooks (spec 002, research §5–§7).

``SentrySpanHook`` turns ``@traced``/``span()`` into Sentry spans whose status tells a timeout, an
open circuit and a code error apart. ``SentryAuditSink`` turns audit records into structured logs
and, through the catalog's metric rules, into unsampled counters. Neither ever raises into the
business call.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import sentry_sdk
from sentry_sdk import logger as sentry_logger
from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.utils import capture_internal_exceptions

from aeropass.adapters.resilience.circuit_breaker import CircuitOpenError
from aeropass.observability import telemetry_catalog as tc

SPAN_OP = "aeropass.step"

_STATUS_BY_ERROR: tuple[tuple[type[BaseException], str], ...] = (
    (TimeoutError, SPANSTATUS.DEADLINE_EXCEEDED),
    (CircuitOpenError, SPANSTATUS.UNAVAILABLE),
    (asyncio.CancelledError, SPANSTATUS.CANCELLED),
)


def span_status(error: BaseException) -> str:
    for kind, status in _STATUS_BY_ERROR:
        if isinstance(error, kind):
            return status
    return SPANSTATUS.INTERNAL_ERROR


class SentrySpanHook:
    @contextlib.contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        error: BaseException | None = None
        with sentry_sdk.start_span(op=SPAN_OP, name=name) as span:
            try:
                yield
            except BaseException as exc:
                # Caught inside the span so its own exit does not overwrite the status.
                span.set_status(span_status(exc))
                error = exc
            else:
                span.set_status(SPANSTATUS.OK)
        if error is not None:
            raise error


def _attributes(data: Mapping[str, Any], keys: Iterable[str]) -> dict[str, str]:
    allowed = set(keys)
    return {
        tc.ATTRIBUTE_NAMES[key]: str(value)
        for key, value in data.items()
        if key in allowed and value is not None
    }


class SentryAuditSink:
    def __call__(self, event: str, data: dict[str, Any]) -> None:
        spec = tc.AUDIT_EVENTS.get(event)
        if spec is None:
            return
        with capture_internal_exceptions():
            if not spec.errors_only or data.get("outcome") == tc.OUTCOME_ERROR:
                attributes = {tc.EVENT_ATTRIBUTE: event, **_attributes(data, spec.allowed_keys)}
                log = sentry_logger.warning if spec.level == "warning" else sentry_logger.info
                log(tc.LOG_BODY, attributes=attributes)
            for rule in tc.METRIC_RULES:
                if rule.event == event and rule.when(data):
                    sentry_sdk.metrics.count(rule.name, 1, attributes=_attributes(data, rule.keys))

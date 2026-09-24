"""Telemetry catalog (spec 002, contracts/telemetry-events.md).

Single source (constitution, Principle IV) of the audit event names, how each one is logged, the
metric rules derived from them and the attribute allowlist. The Sentry sink emits from it, the
privacy filter filters with it and the contract test checks against it. No provider import here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

# --- audit events -------------------------------------------------------------------------
IDENTITY_VERIFICATION = "identity.verification"
CREDENTIAL_ISSUE = "credential.issue"
CREDENTIAL_CONSUME = "credential.consume"
AUTH_AUTHENTICATE = "auth.authenticate"
CIRCUIT_OPENED = "resilience.circuit_opened"

OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"

# --- log shape ----------------------------------------------------------------------------
LOG_BODY = "aeropass.audit"
EVENT_ATTRIBUTE = "aeropass.event"

# Audit payload key -> log/metric attribute name.
ATTRIBUTE_NAMES: Mapping[str, str] = {
    "outcome": "aeropass.outcome",
    "error": "aeropass.error_type",
    "resultado": "aeropass.resultado",
    "motivo": "aeropass.motivo",
    "estado": "aeropass.estado",
    "dependencia": "aeropass.dependencia",
}

# Keys every audit record may carry (set by ``@audited``).
COMMON_KEYS = frozenset({"outcome", "error"})


@dataclass(frozen=True)
class AuditEvent:
    name: str
    level: Literal["info", "warning"] = "info"
    errors_only: bool = False  # log only ``outcome=error`` records
    keys: frozenset[str] = frozenset()  # payload keys allowed besides COMMON_KEYS

    @property
    def allowed_keys(self) -> frozenset[str]:
        return COMMON_KEYS | self.keys


AUDIT_EVENTS: Mapping[str, AuditEvent] = {
    e.name: e
    for e in (
        AuditEvent(IDENTITY_VERIFICATION, keys=frozenset({"resultado", "motivo", "estado"})),
        AuditEvent(CREDENTIAL_ISSUE),
        AuditEvent(CREDENTIAL_CONSUME),
        # One per request: the ok record would only be noise and cost.
        AuditEvent(AUTH_AUTHENTICATE, errors_only=True),
        AuditEvent(CIRCUIT_OPENED, level="warning", keys=frozenset({"dependencia"})),
    )
}

LOG_ATTRIBUTES = frozenset({EVENT_ATTRIBUTE, *ATTRIBUTE_NAMES.values()})


# --- metrics ------------------------------------------------------------------------------
@dataclass(frozen=True)
class MetricRule:
    """A counter emitted (value 1) for every matching audit record."""

    name: str
    event: str
    keys: frozenset[str]  # payload keys copied as attributes (when present)
    when: Callable[[Mapping[str, Any]], bool] = field(default=lambda data: True)

    @property
    def attributes(self) -> frozenset[str]:
        return frozenset(ATTRIBUTE_NAMES[k] for k in self.keys)


# Each user story adds its rule here (tasks T030, T039, T041).
METRIC_RULES: tuple[MetricRule, ...] = (
    # US6 — contingency rate per external dependency (KR A2.5).
    MetricRule("aeropass.circuit_breaker.apertura", CIRCUIT_OPENED, frozenset({"dependencia"})),
    # US4 — self-service rate (KR A1.2): one per passenger reaching a final state.
    MetricRule(
        "aeropass.pasajero.estado_final",
        IDENTITY_VERIFICATION,
        frozenset({"estado"}),
        when=lambda data: data.get("outcome") == OUTCOME_OK and data.get("estado") is not None,
    ),
    # US5 — auto-rejection rate: one per attempt, by result and reason. Unhandled errors never
    # count here (they are ``outcome=error``, measured apart as error events).
    MetricRule(
        "aeropass.verificacion.intento",
        IDENTITY_VERIFICATION,
        frozenset({"resultado", "motivo"}),
        when=lambda data: data.get("outcome") == OUTCOME_OK and data.get("resultado") is not None,
    ),
)


def metric_attributes() -> Mapping[str, frozenset[str]]:
    """Metric name -> allowed attribute names."""
    return {rule.name: rule.attributes for rule in METRIC_RULES}

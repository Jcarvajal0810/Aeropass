"""Emitted telemetry == contracts/telemetry-events.md (spec 002, FR-006). Written before the sink.

A new audit key, event or metric that is not in the catalog must never reach Sentry, and the
catalog must stay documented in the contract.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from aeropass.adapters.faults.context import KNOWN_FAULTS
from aeropass.domain.enums import EstadoPasajero, MotivoFallo, ResultadoIntento
from aeropass.observability import telemetry_catalog as tc
from aeropass.observability.hooks import emit_audit

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "002-observabilidad-sentry"
    / "contracts"
    / "telemetry-events.md"
)

# Every value each payload key can take (None = absent), plus a key that must never pass.
_VALUES = {
    "resultado": [r.value for r in ResultadoIntento],
    "motivo": [None, *(m.value for m in MotivoFallo)],
    "estado": [
        None,
        EstadoPasajero.VERIFICADO.value,
        EstadoPasajero.REQUIERE_REVISION_MANUAL.value,
    ],
    "dependencia": ["biometric", "qstash"],
    "fault": sorted(KNOWN_FAULTS),
}
_FORBIDDEN = {"numero_documento": "1234567890", "token": "eyJ.secret"}


def _emit_every_combination() -> None:
    for spec in tc.AUDIT_EVENTS.values():
        keys = sorted(spec.keys)
        for values in itertools.product(*(_VALUES[k] for k in keys)):
            payload = {k: v for k, v in zip(keys, values, strict=True) if v is not None}
            emit_audit(spec.name, outcome=tc.OUTCOME_OK, **payload, **_FORBIDDEN)
        emit_audit(spec.name, outcome=tc.OUTCOME_ERROR, error="RuntimeError", **_FORBIDDEN)
    emit_audit("not.in.catalog", outcome=tc.OUTCOME_OK)


def test_audit_logs_only_carry_allowlisted_attributes(sentry_capture):
    _emit_every_combination()

    logs = sentry_capture.audit_logs()
    assert logs, "audit events must produce logs"
    for log in logs:
        ours = {k for k in log["attributes"] if k.startswith("aeropass.")}
        assert ours <= tc.LOG_ATTRIBUTES, ours - tc.LOG_ATTRIBUTES
        assert log["attributes"][tc.EVENT_ATTRIBUTE] in tc.AUDIT_EVENTS


def test_metrics_only_come_from_catalog_rules_with_their_attributes(sentry_capture):
    _emit_every_combination()

    allowed = tc.metric_attributes()
    for metric in sentry_capture.metrics:
        assert metric["name"] in allowed
        ours = {k for k in metric["attributes"] if k.startswith("aeropass.")}
        assert ours <= allowed[metric["name"]]


def test_forbidden_values_never_leave_the_process(sentry_capture):
    _emit_every_combination()

    dump = sentry_capture.dump()
    for value in _FORBIDDEN.values():
        assert value not in dump


def test_catalog_is_documented_in_the_contract():
    text = CONTRACT.read_text(encoding="utf-8")

    for name in tc.AUDIT_EVENTS:
        assert f"`{name}`" in text, name
    for attribute in tc.LOG_ATTRIBUTES:
        assert f"`{attribute}`" in text, attribute
    for rule in tc.METRIC_RULES:
        assert f"`{rule.name}`" in text, rule.name

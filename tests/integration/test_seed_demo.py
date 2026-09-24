"""US9 — demo data for the 15-minute presentation (spec 002, FR-019, research §12)."""

from __future__ import annotations

import pytest

from aeropass.observability import telemetry_catalog as tc
from aeropass.tools.seed_demo import DemoRefused, seed

pytestmark = pytest.mark.usefixtures("db")


def _attributes(capture, name: str, attribute: str) -> list[str]:
    return [m["attributes"][attribute] for m in capture.metrics_named(name)]


async def test_the_default_run_produces_every_business_signal(container, sentry_capture):
    summary = await seed(container, verificados=3, out=lambda *_: None)

    estados = _attributes(sentry_capture, "aeropass.pasajero.estado_final", "aeropass.estado")
    assert estados.count("VERIFICADO") == summary.verificados >= 1
    assert estados.count("REQUIERE_REVISION_MANUAL") == summary.revision_manual >= 1
    motivos = {
        m["attributes"].get("aeropass.motivo")
        for m in sentry_capture.metrics_named("aeropass.verificacion.intento")
        if m["attributes"]["aeropass.resultado"] == "FALLIDO"
    }
    assert motivos == {"LIVENESS", "COMPARACION"}
    resultados = _attributes(sentry_capture, "aeropass.verificacion.intento", "aeropass.resultado")
    assert "NO_CONCLUYENTE" in resultados
    assert {"/v1/passes", "/v1/passes/{credencial_id}"} <= {
        t["transaction"] for t in sentry_capture.transactions
    }
    assert sentry_capture.metrics_named("aeropass.circuit_breaker.apertura") == []
    assert sentry_capture.events == []


async def test_the_printed_rates_match_the_metrics(container, sentry_capture):
    summary = await seed(container, verificados=3, out=lambda *_: None)

    total = summary.verificados + summary.revision_manual
    assert summary.autoservicio == pytest.approx(summary.verificados / total)
    resultados = _attributes(sentry_capture, "aeropass.verificacion.intento", "aeropass.resultado")
    fallidos, exitosos = resultados.count("FALLIDO"), resultados.count("EXITOSO")
    assert summary.auto_rechazo == pytest.approx(fallidos / (fallidos + exitosos))


async def test_contingency_opens_the_biometric_circuit(container, sentry_capture):
    await seed(container, verificados=1, contingencia=True, out=lambda *_: None)

    dependencias = _attributes(
        sentry_capture, "aeropass.circuit_breaker.apertura", "aeropass.dependencia"
    )
    assert dependencias == ["biometric"]
    assert sentry_capture.audit_logs(tc.CIRCUIT_OPENED)


async def test_solo_error_raises_exactly_one_unhandled_error(container, sentry_capture):
    await seed(container, solo_error=True, out=lambda *_: None)

    [event] = sentry_capture.events
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert sentry_capture.metrics == []


async def test_it_refuses_to_run_against_prod(container):
    container.settings.sentry_environment = "prod"

    with pytest.raises(DemoRefused):
        await seed(container, out=lambda *_: None)

"""Prepare Sentry data for the 15-minute presentation (spec 002, FR-019).

See specs/002-observabilidad-sentry/research.md §12 and the README "Observability" section.

Usage::

    SENTRY_DSN=<dsn> SENTRY_ENVIRONMENT=demo DATABASE_URL=<non-prod database> \
        uv run --python 3.11 python -m aeropass.tools.seed_demo [--verificados 10] [--contingencia]
    ... python -m aeropass.tools.seed_demo --solo-error   # one unhandled error (alert B1, live)

Runs the passenger flow in process: fake adapters (no Clerk, Blob, Redis or QStash), the mock
biometric provider driven by ``MOCK:<marker>`` selfies and the real Postgres in DATABASE_URL
(migrations applied). Telemetry goes to Sentry exactly as in production. It refuses to run with
SENTRY_ENVIRONMENT=prod. Run it at least one hour before presenting so hourly widgets have data.

``--contingencia`` makes five provider timeouts in a row, which opens the biometric circuit
(alert B3). It runs last: while the circuit is open every verification is inconclusive.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from aeropass.adapters.fakes.images import make_image
from aeropass.adapters.observability.sentry_setup import flush_telemetry
from aeropass.api.deps import Container
from aeropass.config import Settings
from aeropass.main import create_app

FLIGHT_CODE = "AV9123"
DEMO_BIOMETRIC_TIMEOUT_SECONDS = 0.5
CONTINGENCY_TIMEOUTS = 5  # the circuit breaker's failure threshold
ERROR_PATH = "/demo/error"

# Selfie markers of each passenger profile (MockBiometricAdapter).
VERIFIED = ("ok",)
VERIFIED_AFTER_REJECTION = ("spoof", "ok")
VERIFIED_AFTER_TIMEOUT = ("timeout", "ok")
MANUAL_REVIEW = ("spoof", "other", "spoof")

Out = Callable[..., Any]


class DemoRefused(RuntimeError):
    pass


@dataclass
class DemoSummary:
    verificados: int = 0
    revision_manual: int = 0
    exitosos: int = 0
    fallidos: int = 0
    no_concluyentes: int = 0
    pases: int = 0

    @property
    def autoservicio(self) -> float:
        total = self.verificados + self.revision_manual
        return self.verificados / total if total else 0.0

    @property
    def auto_rechazo(self) -> float:
        concluded = self.exitosos + self.fallidos
        return self.fallidos / concluded if concluded else 0.0


class _Demo:
    def __init__(self, client: httpx.AsyncClient, summary: DemoSummary) -> None:
        self._client = client
        self._summary = summary
        self._run = secrets.token_hex(3)
        self._count = 0

    def _new_user(self) -> str:
        self._count += 1
        return f"demo-{self._run}-{self._count}"

    def _headers(self, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer test:{user}"}

    async def _register(self, user: str) -> None:
        response = await self._client.post(
            "/v1/identity",
            data={
                "nombre_completo": f"Pasajero Demo {self._count}",
                "tipo_documento": "CC",
                "numero_documento": f"8{zlib.crc32(user.encode()) % 10**9:09d}",
                "fecha_vencimiento": "2030-01-01",
            },
            files={"foto_documento": ("documento.jpg", make_image("documento"), "image/jpeg")},
            headers=self._headers(user),
        )
        response.raise_for_status()

    async def _verify(self, user: str, marker: str) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/biometrics/verifications",
            files={"selfie": ("selfie.jpg", make_image(marker), "image/jpeg")},
            headers=self._headers(user),
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        resultado = body["resultado"]
        if resultado == "EXITOSO":
            self._summary.exitosos += 1
        elif resultado == "FALLIDO":
            self._summary.fallidos += 1
        else:
            self._summary.no_concluyentes += 1
        return body

    async def _pass(self, user: str) -> None:
        issued = await self._client.post(
            "/v1/passes", json={"codigo_vuelo": FLIGHT_CODE}, headers=self._headers(user)
        )
        issued.raise_for_status()
        credencial_id = issued.json()["credencial_id"]
        detail = await self._client.get(f"/v1/passes/{credencial_id}", headers=self._headers(user))
        detail.raise_for_status()
        self._summary.pases += 1

    async def passenger(self, markers: tuple[str, ...]) -> None:
        user = self._new_user()
        await self._register(user)
        estado = None
        for marker in markers:
            estado = (await self._verify(user, marker))["estado_pasajero"]
        if estado == "VERIFICADO":
            self._summary.verificados += 1
            await self._pass(user)
        elif estado == "REQUIERE_REVISION_MANUAL":
            self._summary.revision_manual += 1

    async def contingency(self) -> None:
        user = self._new_user()
        await self._register(user)
        for _ in range(CONTINGENCY_TIMEOUTS):
            await self._verify(user, "timeout")  # inconclusive: the passenger keeps retrying


async def seed(
    container: Container,
    *,
    verificados: int = 10,
    contingencia: bool = False,
    solo_error: bool = False,
    out: Out = print,
) -> DemoSummary:
    """Drive the demo flows through the app; the caller owns the container's settings."""
    if container.settings.sentry_environment == "prod":
        raise DemoRefused("seed_demo never runs against SENTRY_ENVIRONMENT=prod")

    app = create_app(container)
    summary = DemoSummary()
    if solo_error:

        @app.get(ERROR_PATH, include_in_schema=False)
        async def demo_error() -> None:
            raise RuntimeError("AeroPass demo: unhandled error on purpose")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
            if solo_error:
                response = await client.get(ERROR_PATH)
                out(f"Unhandled error sent (HTTP {response.status_code}): expect alert B1")
                return summary

            demo = _Demo(client, summary)
            for _ in range(verificados):
                await demo.passenger(VERIFIED)
            for profile in (VERIFIED_AFTER_REJECTION, VERIFIED_AFTER_TIMEOUT, MANUAL_REVIEW):
                for _ in range(2):
                    await demo.passenger(profile)
            if contingencia:
                await demo.contingency()
                out("Biometric circuit opened: expect alert B3")
    finally:
        flush_telemetry()

    out(
        f"Passengers: {summary.verificados} verified, {summary.revision_manual} manual review; "
        f"{summary.pases} passes issued"
    )
    out(
        f"Attempts: {summary.exitosos} successful, {summary.fallidos} rejected, "
        f"{summary.no_concluyentes} inconclusive"
    )
    out(f"Expected self-service rate (W3): {summary.autoservicio:.1%}")
    out(f"Expected auto-rejection rate (W4): {summary.auto_rechazo:.1%}")
    return summary


def _container(settings: Settings) -> Container:
    demo_settings = settings.model_copy(
        update={
            "aeropass_adapters": "fake",
            "biometric_provider": "mock",
            "biometric_timeout_seconds": DEMO_BIOMETRIC_TIMEOUT_SECONDS,
        }
    )
    return Container(demo_settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verificados", type=int, default=10, help="passengers verified first try")
    parser.add_argument("--contingencia", action="store_true", help="open the biometric circuit")
    parser.add_argument("--solo-error", action="store_true", help="only one unhandled error")
    args = parser.parse_args(argv)

    settings = Settings()
    if not settings.sentry_dsn:
        print("SENTRY_DSN is not set: nothing would reach Sentry", file=sys.stderr)
        return 2
    print(f"Sentry environment: {settings.sentry_environment}")
    try:
        asyncio.run(
            seed(
                _container(settings),
                verificados=args.verificados,
                contingencia=args.contingencia,
                solo_error=args.solo_error,
            )
        )
    except DemoRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

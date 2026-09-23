"""Deterministic provider driven by the ``MOCK:<marker>`` inside the selfie bytes (research R4)."""

import asyncio

from aeropass.adapters.fakes.images import read_marker
from aeropass.domain.images import ImageInput
from aeropass.domain.verification import BiometricResult
from aeropass.observability.hooks import traced
from aeropass.ports.biometric_provider import ProviderUnavailable

_SCORES: dict[str, tuple[float, float]] = {
    "ok": (0.95, 0.93),
    "spoof": (0.20, 0.90),
    "other": (0.95, 0.30),
}


class MockBiometricAdapter:
    name = "mock"

    def __init__(self, hang_seconds: float = 30.0) -> None:
        self._hang_seconds = hang_seconds
        self.calls = 0
        self.last_reference: ImageInput | None = None

    @traced("biometrics.mock.evaluate")
    async def evaluate(self, selfie: ImageInput, referencia: ImageInput) -> BiometricResult:
        self.calls += 1
        if not referencia.data:
            raise ProviderUnavailable("empty reference image")
        self.last_reference = referencia
        marker = read_marker(selfie.data) or "ok"
        if marker == "timeout":
            await asyncio.sleep(self._hang_seconds)  # the breaker's timeout fires first
            raise ProviderUnavailable("mock timeout")
        liveness, comparacion = _SCORES.get(marker, _SCORES["ok"])
        return BiometricResult(liveness, comparacion, proveedor=self.name)

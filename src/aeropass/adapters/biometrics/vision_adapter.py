"""Adapter for an HTTP vision provider: translates its payload to ``BiometricResult``.

Expected provider contract (generic; adjust the mapping in ``_translate`` when a concrete vendor
is contracted): ``POST {VISION_PROVIDER_URL}`` multipart ``selfie`` + ``reference`` →
``{"liveness": {"score": float}, "match": {"score": float}}``.
Timeouts are enforced by the circuit breaker wrapping this adapter.
"""

from typing import Any

import httpx

from aeropass.domain.images import ImageInput
from aeropass.domain.verification import BiometricResult
from aeropass.observability.hooks import traced
from aeropass.ports.biometric_provider import ProviderUnavailable


class VisionProviderAdapter:
    name = "vision"

    def __init__(self, client: httpx.AsyncClient, url: str, api_key: str) -> None:
        self._client = client
        self._url = url
        self._api_key = api_key

    @traced("biometrics.vision.evaluate")
    async def evaluate(self, selfie: ImageInput, referencia: ImageInput) -> BiometricResult:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={
                    "selfie": ("selfie", selfie.data, selfie.content_type),
                    "reference": ("reference", referencia.data, referencia.content_type),
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(type(exc).__name__) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable(f"provider status {response.status_code}")
        if response.status_code >= 400:
            raise ProviderUnavailable(f"provider rejected request ({response.status_code})")
        try:
            return self._translate(response.json())
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderUnavailable("malformed provider response") from exc

    def _translate(self, payload: dict[str, Any]) -> BiometricResult:
        liveness = float(payload["liveness"]["score"])
        match = float(payload["match"]["score"])
        if not (0 <= liveness <= 1 and 0 <= match <= 1):
            raise ValueError("scores out of range")
        return BiometricResult(liveness, match, proveedor=self.name)

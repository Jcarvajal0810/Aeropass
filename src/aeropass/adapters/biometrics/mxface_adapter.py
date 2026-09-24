"""Adapter for the MxFace API (faceapi.mxface.ai): translates its payloads to ``BiometricResult``.

This is the only module that knows MxFace exists; the rest of the app only sees the
``BiometricProvider`` port. Each ``evaluate`` makes two calls (two transactions of the quota):

- ``POST /face/verify``   → 1:1 match selfie vs document photo. ``matchResult`` (1/0) is MxFace's
  own verdict, mapped to ``score_comparacion`` 1.0/0.0. An empty ``matchedFaces`` (no face found in
  either image) is a non-match.
- ``POST /face/Liveness`` → ``livenessScore`` (0–100) on the selfie, normalized to 0–1.

Thresholds are applied by the domain (``BIOMETRIC_LIVENESS_THRESHOLD``); timeouts by the circuit
breaker wrapping this adapter. Free tier ("TESTER"): 4 transactions/day per API.
"""

import asyncio
import base64
from typing import Any

import httpx

from aeropass.domain.images import ImageInput
from aeropass.domain.verification import BiometricResult
from aeropass.observability.hooks import traced
from aeropass.ports.biometric_provider import ProviderUnavailable

MXFACE_BASE_URL = "https://faceapi.mxface.ai/api/v3"


def _b64(image: ImageInput) -> str:
    return base64.b64encode(image.data).decode("ascii")


class MxFaceAdapter:
    name = "mxface"

    def __init__(
        self, client: httpx.AsyncClient, subscription_key: str, base_url: str = MXFACE_BASE_URL
    ) -> None:
        self._client = client
        self._subscription_key = subscription_key
        self._base_url = base_url.rstrip("/")

    @traced("biometrics.mxface.evaluate")
    async def evaluate(self, selfie: ImageInput, referencia: ImageInput) -> BiometricResult:
        try:
            async with asyncio.TaskGroup() as tg:
                verify = tg.create_task(
                    self._post(
                        "/face/verify",
                        {"encoded_image1": _b64(selfie), "encoded_image2": _b64(referencia)},
                    )
                )
                liveness = tg.create_task(
                    self._post("/face/Liveness", {"encoded_image": _b64(selfie)})
                )
        except* ProviderUnavailable as group:
            raise group.exceptions[0] from None
        try:
            return BiometricResult(
                score_liveness=self._liveness_score(liveness.result()),
                score_comparacion=self._match_score(verify.result()),
                proveedor=self.name,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderUnavailable("malformed provider response") from exc

    async def _post(self, path: str, body: dict[str, str]) -> Any:
        try:
            response = await self._client.post(
                self._base_url + path,
                headers={"Subscriptionkey": self._subscription_key},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(type(exc).__name__) from exc
        if response.status_code >= 400:
            raise ProviderUnavailable(f"provider status {response.status_code} on {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderUnavailable("malformed provider response") from exc

    @staticmethod
    def _match_score(payload: dict[str, Any]) -> float:
        matched = payload.get("matchedFaces") or []
        if not matched:
            return 0.0
        return 1.0 if matched[0]["matchResult"] == 1 else 0.0

    @staticmethod
    def _liveness_score(payload: dict[str, Any]) -> float:
        score = float(payload["livenessScore"])
        if not 0 <= score <= 100:
            raise ValueError("liveness score out of range")
        return score / 100

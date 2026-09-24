"""MxFaceAdapter against the real shape of MxFace responses, served by an httpx MockTransport.

No network, no quota spent.
"""

import base64
import json

import httpx
import pytest

from aeropass.adapters.biometrics.factory import BiometricProviderFactory
from aeropass.adapters.biometrics.mxface_adapter import MxFaceAdapter
from aeropass.adapters.resilience.circuit_breaker import CircuitBreaker, InMemoryBreakerStateStore
from aeropass.config import Settings
from aeropass.domain.enums import MotivoFallo, ResultadoIntento
from aeropass.domain.images import ImageInput
from aeropass.domain.verification import Thresholds, evaluate_outcome
from aeropass.ports.biometric_provider import ProviderUnavailable

SELFIE = ImageInput(b"\xff\xd8selfie", "image/jpeg")
DOCUMENTO = ImageInput(b"\xff\xd8documento", "image/jpeg")
T = Thresholds(liveness=0.50, comparacion=0.80)

MATCH = {"matchedFaces": [{"matchResult": 1, "image1_face": {}, "image2_face": {}}]}
NO_MATCH = {"matchedFaces": [{"matchResult": 0, "image1_face": {}, "image2_face": {}}]}
NO_FACE = {"matchedFaces": []}
LIVE = {"livenessScore": 83.39}
SPOOF = {"livenessScore": 12.0}


def adapter(verify, liveness, requests: list[httpx.Request] | None = None) -> MxFaceAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path.endswith("/face/verify"):
            return (
                verify if isinstance(verify, httpx.Response) else httpx.Response(200, json=verify)
            )
        if request.url.path.endswith("/face/Liveness"):
            return (
                liveness
                if isinstance(liveness, httpx.Response)
                else httpx.Response(200, json=liveness)
            )
        raise AssertionError(f"unexpected endpoint: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MxFaceAdapter(client, "fake-key")


@pytest.mark.parametrize(
    ("verify", "liveness", "expected"),
    [
        (MATCH, LIVE, (ResultadoIntento.EXITOSO, None)),
        (NO_MATCH, LIVE, (ResultadoIntento.FALLIDO, MotivoFallo.COMPARACION)),
        (NO_FACE, LIVE, (ResultadoIntento.FALLIDO, MotivoFallo.COMPARACION)),
        (MATCH, SPOOF, (ResultadoIntento.FALLIDO, MotivoFallo.LIVENESS)),
    ],
)
async def test_translates_mxface_payloads(verify, liveness, expected):
    result = await adapter(verify, liveness).evaluate(SELFIE, DOCUMENTO)
    assert result.proveedor == "mxface"
    assert evaluate_outcome(result, T) == expected


async def test_normalizes_liveness_to_unit_range():
    result = await adapter(MATCH, LIVE).evaluate(SELFIE, DOCUMENTO)
    assert result.score_liveness == pytest.approx(0.8339)
    assert result.score_comparacion == 1.0


async def test_sends_key_and_base64_images():
    requests: list[httpx.Request] = []
    await adapter(MATCH, LIVE, requests).evaluate(SELFIE, DOCUMENTO)
    by_path = {r.url.path.rsplit("/", 1)[-1]: r for r in requests}
    assert by_path.keys() == {"verify", "Liveness"}
    assert all(r.headers["Subscriptionkey"] == "fake-key" for r in requests)
    verify_body = json.loads(by_path["verify"].content)
    assert base64.b64decode(verify_body["encoded_image1"]) == SELFIE.data
    assert base64.b64decode(verify_body["encoded_image2"]) == DOCUMENTO.data
    liveness_body = json.loads(by_path["Liveness"].content)
    assert base64.b64decode(liveness_body["encoded_image"]) == SELFIE.data


@pytest.mark.parametrize(
    ("verify", "liveness"),
    [
        (httpx.Response(500), LIVE),
        (MATCH, httpx.Response(429)),
        (httpx.Response(401, json={"message": "invalid key"}), LIVE),
        (MATCH, {"unexpected": True}),
        (MATCH, {"livenessScore": 180}),
        (httpx.Response(200, content=b"not json"), LIVE),
    ],
)
async def test_failures_raise_provider_unavailable(verify, liveness):
    with pytest.raises(ProviderUnavailable):
        await adapter(verify, liveness).evaluate(SELFIE, DOCUMENTO)


async def test_network_error_raises_provider_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable):
        await MxFaceAdapter(client, "fake-key").evaluate(SELFIE, DOCUMENTO)


def test_factory_builds_mxface(fake_clock):
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        biometric_provider="mxface",
        mxface_subscription_key="k",
    )
    breaker = CircuitBreaker("biometric", InMemoryBreakerStateStore(), fake_clock)
    provider = BiometricProviderFactory.create(settings, breaker)
    assert isinstance(provider.inner, MxFaceAdapter)
    assert provider.name == "mxface"

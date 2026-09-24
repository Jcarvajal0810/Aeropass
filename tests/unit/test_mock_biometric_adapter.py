"""MockBiometricAdapter + domain rules: the business flow without any real provider."""

import pytest

from aeropass.adapters.biometrics.mock_adapter import MockBiometricAdapter
from aeropass.adapters.fakes.images import make_image
from aeropass.domain.enums import MotivoFallo, ResultadoIntento
from aeropass.domain.images import ImageInput
from aeropass.domain.verification import Thresholds, evaluate_outcome
from aeropass.ports.biometric_provider import ProviderUnavailable

T = Thresholds(liveness=0.80, comparacion=0.80)
DOCUMENTO = ImageInput(make_image(None), "image/jpeg")


def selfie(marker: str | None) -> ImageInput:
    return ImageInput(make_image(marker), "image/jpeg")


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("ok", (ResultadoIntento.EXITOSO, None)),
        (None, (ResultadoIntento.EXITOSO, None)),  # approves by default
        ("spoof", (ResultadoIntento.FALLIDO, MotivoFallo.LIVENESS)),
        ("other", (ResultadoIntento.FALLIDO, MotivoFallo.COMPARACION)),
    ],
)
async def test_marker_drives_outcome(marker, expected):
    result = await MockBiometricAdapter().evaluate(selfie(marker), DOCUMENTO)
    assert evaluate_outcome(result, T) == expected


async def test_timeout_marker_raises_provider_unavailable():
    with pytest.raises(ProviderUnavailable):
        await MockBiometricAdapter(hang_seconds=0).evaluate(selfie("timeout"), DOCUMENTO)

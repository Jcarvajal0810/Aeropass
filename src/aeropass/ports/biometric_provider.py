from typing import Protocol

from aeropass.domain.images import ImageInput
from aeropass.domain.verification import BiometricResult


class ProviderUnavailable(Exception):
    """The biometric provider failed (5xx, timeout, malformed response)."""


class BiometricProvider(Protocol):
    name: str

    async def evaluate(self, selfie: ImageInput, referencia: ImageInput) -> BiometricResult:
        """Liveness of ``selfie`` + face match of ``selfie`` against ``referencia``."""
        ...


class SafeBiometricProvider(Protocol):
    """Provider already protected by a circuit breaker: ``None`` means "no verdict"."""

    name: str

    async def evaluate(
        self, selfie: ImageInput, referencia: ImageInput
    ) -> BiometricResult | None: ...

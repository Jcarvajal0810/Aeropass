"""Factory + circuit-breaker wrapper for the biometric provider (constitution: Factory, CB)."""

from __future__ import annotations

import logging

import httpx

from aeropass.adapters.biometrics.mock_adapter import MockBiometricAdapter
from aeropass.adapters.biometrics.vision_adapter import VisionProviderAdapter
from aeropass.adapters.resilience.circuit_breaker import CircuitBreaker, CircuitOpenError
from aeropass.config import Settings
from aeropass.domain.images import ImageInput
from aeropass.domain.verification import BiometricResult
from aeropass.ports.biometric_provider import BiometricProvider, ProviderUnavailable

logger = logging.getLogger(__name__)


class ResilientBiometricProvider:
    """Never blocks or raises: returns ``None`` (→ NO_CONCLUYENTE) when the provider fails."""

    def __init__(self, inner: BiometricProvider, breaker: CircuitBreaker, timeout: float) -> None:
        self.inner = inner
        self.name = inner.name
        self._breaker = breaker
        self._timeout = timeout

    async def evaluate(self, selfie: ImageInput, referencia: ImageInput) -> BiometricResult | None:
        try:
            return await self._breaker.call(
                lambda: self.inner.evaluate(selfie, referencia), timeout=self._timeout
            )
        except (ProviderUnavailable, CircuitOpenError, TimeoutError) as exc:
            logger.warning("biometric provider unavailable: %s", type(exc).__name__)
            return None


class BiometricProviderFactory:
    @staticmethod
    def create(settings: Settings, breaker: CircuitBreaker) -> ResilientBiometricProvider:
        inner: BiometricProvider
        if settings.biometric_provider == "vision":
            inner = VisionProviderAdapter(
                httpx.AsyncClient(),
                settings.vision_provider_url,
                settings.vision_provider_api_key,
            )
        else:
            inner = MockBiometricAdapter()
        return ResilientBiometricProvider(inner, breaker, settings.biometric_timeout_seconds)

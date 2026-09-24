"""Composition root: the ONLY module that knows concrete adapters (constitution III).

``Container`` builds every collaborator lazily (``cached_property``) so a cold start only pays
for what the request uses. ``AEROPASS_ADAPTERS=fake`` swaps the external services (Clerk, Blob,
Redis, QStash) for in-memory doubles; Postgres is always real (plan.md "Adapter modes").
Tests may assign any attribute directly to override it.
"""

from __future__ import annotations

from functools import cached_property
from typing import Protocol

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aeropass.adapters.biometrics.factory import (
    BiometricProviderFactory,
    ResilientBiometricProvider,
)
from aeropass.adapters.clock import SystemClock
from aeropass.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from aeropass.adapters.flights.format_only_catalog import FormatOnlyFlightCatalog
from aeropass.adapters.qstash.publisher import ResilientEventPublisher
from aeropass.adapters.redis.token_proxy import RedisVerificationProxy
from aeropass.adapters.resilience.circuit_breaker import (
    BreakerStateStore,
    CircuitBreaker,
    InMemoryBreakerStateStore,
)
from aeropass.config import Settings, get_settings
from aeropass.domain.credential.signing import CredentialSigner, CredentialVerifier
from aeropass.domain.verification import Thresholds
from aeropass.ports.auth import AuthenticatedUser, Authenticator
from aeropass.ports.clock import Clock
from aeropass.ports.event_publisher import EventPublisher
from aeropass.ports.flight_catalog import FlightCatalog
from aeropass.ports.health import HealthCheck
from aeropass.ports.media_storage import MediaStorage
from aeropass.ports.rate_limiter import RateLimiter
from aeropass.ports.token_store import TokenStore
from aeropass.services.biometric_verification_service import BiometricVerificationService
from aeropass.services.credential_lifecycle_service import CredentialLifecycleService
from aeropass.services.health_service import HealthService
from aeropass.services.identity_service import IdentityService
from aeropass.services.identity_verification_facade import IdentityVerificationFacade
from aeropass.services.outbox_dispatcher import OutboxDispatcher
from aeropass.services.pass_issuance_service import PassIssuanceService
from aeropass.services.registration_service import RegistrationService


class SignatureVerifier(Protocol):
    async def verify(self, request: Request) -> None: ...


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def from_env(cls) -> Container:
        return cls(get_settings())

    @property
    def fake(self) -> bool:
        return self.settings.aeropass_adapters == "fake"

    # --- infrastructure -----------------------------------------------------------------
    @cached_property
    def clock(self) -> Clock:
        return SystemClock()

    @cached_property
    def authenticator(self) -> Authenticator:
        if self.fake:
            from aeropass.adapters.fakes.auth import FakeAuth

            return FakeAuth()
        from aeropass.adapters.auth.clerk import ClerkAuthenticator

        return ClerkAuthenticator(self.settings.clerk_secret_key, self.settings.authorized_parties)

    @cached_property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        from aeropass.adapters.db.engine import get_session_factory

        return get_session_factory()

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    @cached_property
    def breaker_store(self) -> BreakerStateStore:
        if self.fake:
            return InMemoryBreakerStateStore()
        from aeropass.adapters.redis.breaker_state import RedisBreakerStateStore
        from aeropass.adapters.redis.client import get_redis

        return RedisBreakerStateStore(get_redis())

    def breaker(self, name: str) -> CircuitBreaker:
        return CircuitBreaker(name, self.breaker_store, self.clock)

    @cached_property
    def media_storage(self) -> MediaStorage:
        if self.fake:
            from aeropass.adapters.fakes.media_storage import InMemoryMediaStorage

            return InMemoryMediaStorage()
        from aeropass.adapters.blob.client import get_blob_client
        from aeropass.adapters.blob.vercel_blob_storage import VercelBlobStorage

        return VercelBlobStorage(get_blob_client(), self.settings.blob_timeout_seconds)

    @cached_property
    def biometric_provider(self) -> ResilientBiometricProvider:
        return BiometricProviderFactory.create(self.settings, self.breaker("biometric"))

    @cached_property
    def event_publisher(self) -> ResilientEventPublisher:
        inner: EventPublisher
        if self.fake:
            from aeropass.adapters.fakes.event_publisher import InMemoryEventPublisher

            inner = InMemoryEventPublisher()
        else:
            from aeropass.adapters.qstash.client import get_qstash
            from aeropass.adapters.qstash.publisher import QStashEventPublisher

            inner = QStashEventPublisher(get_qstash(), self.settings.qstash_events_url_group)
        return ResilientEventPublisher(
            inner, self.breaker("qstash"), self.settings.qstash_timeout_seconds
        )

    @cached_property
    def qstash_verifier(self) -> SignatureVerifier:
        if self.fake:
            from aeropass.adapters.fakes.qstash_receiver import FakeQStashVerifier

            return FakeQStashVerifier()
        from aeropass.adapters.qstash.receiver import QStashSignatureVerifier

        return QStashSignatureVerifier(
            self.settings.qstash_current_signing_key,
            self.settings.qstash_next_signing_key,
            self.settings.public_base_url or None,
        )

    @cached_property
    def token_store(self) -> TokenStore:
        if self.fake:
            from aeropass.adapters.fakes.token_store import FakeTokenStore

            return FakeTokenStore(self.clock)
        from aeropass.adapters.redis.client import get_redis
        from aeropass.adapters.redis.token_store import UpstashTokenStore

        return UpstashTokenStore(get_redis())

    @cached_property
    def token_proxy(self) -> RedisVerificationProxy:
        return RedisVerificationProxy(self.token_store)

    @cached_property
    def rate_limiter(self) -> RateLimiter:
        if self.fake:
            from aeropass.adapters.fakes.rate_limiter import FakeRateLimiter

            return FakeRateLimiter(self.clock)
        from aeropass.adapters.redis.client import get_redis
        from aeropass.adapters.redis.rate_limiter import UpstashRateLimiter

        return UpstashRateLimiter(get_redis())

    @cached_property
    def flight_catalog(self) -> FlightCatalog:
        return FormatOnlyFlightCatalog()

    @cached_property
    def signer(self) -> CredentialSigner:
        key = self.settings.qr_signing_private_key.replace("\\n", "\n").strip()
        if key:
            return CredentialSigner.from_pem(key, self.settings.qr_signing_kid)
        if self.fake:  # local/dev only: ephemeral key, tokens die with the process
            return CredentialSigner.generate(kid=f"{self.settings.qr_signing_kid}-ephemeral")
        raise RuntimeError(
            "QR_SIGNING_PRIVATE_KEY is not configured "
            "(generate one with: python -m aeropass.tools.gen_signing_key)"
        )

    @cached_property
    def credential_verifier(self) -> CredentialVerifier:
        return CredentialVerifier.from_signer(self.signer)

    # --- services ------------------------------------------------------------------------
    @cached_property
    def registration_service(self) -> RegistrationService:
        return RegistrationService(self.uow, self.media_storage, self.clock)

    @cached_property
    def verification_service(self) -> BiometricVerificationService:
        thresholds = Thresholds(
            liveness=self.settings.biometric_liveness_threshold,
            comparacion=self.settings.biometric_match_threshold,
        )
        return BiometricVerificationService(thresholds, self.clock)

    @cached_property
    def identity_service(self) -> IdentityService:
        return IdentityService(self.clock)

    @cached_property
    def outbox_dispatcher(self) -> OutboxDispatcher:
        return OutboxDispatcher(self.uow, self.event_publisher, self.clock)

    @cached_property
    def lifecycle_service(self) -> CredentialLifecycleService:
        return CredentialLifecycleService(self.uow, self.token_store, self.clock)

    @cached_property
    def pass_issuance_service(self) -> PassIssuanceService:
        return PassIssuanceService(
            self.uow,
            tokens=self.token_store,
            rate_limiter=self.rate_limiter,
            flights=self.flight_catalog,
            signer=self.signer,
            lifecycle=self.lifecycle_service,
            clock=self.clock,
            ttl_seconds=self.settings.qr_ttl_seconds,
        )

    @cached_property
    def health_service(self) -> HealthService:
        from aeropass.adapters.health.checks import DatabaseHealthCheck, RedisHealthCheck

        checks: list[HealthCheck] = [DatabaseHealthCheck(self.session_factory)]
        if not self.fake:  # fake mode has no real Redis to check
            from aeropass.adapters.redis.client import get_redis

            checks.append(RedisHealthCheck(get_redis()))
        return HealthService(checks)

    @cached_property
    def facade(self) -> IdentityVerificationFacade:
        return IdentityVerificationFacade(
            self.uow,
            self.media_storage,
            self.biometric_provider,
            self.verification_service,
            self.identity_service,
            self.outbox_dispatcher,
        )


# --- FastAPI dependencies -----------------------------------------------------------------


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


async def get_current_user(
    request: Request, container: Container = Depends(get_container)
) -> AuthenticatedUser:
    return await container.authenticator.authenticate(request)


def get_registration_service(container: Container = Depends(get_container)) -> RegistrationService:
    return container.registration_service


def get_facade(container: Container = Depends(get_container)) -> IdentityVerificationFacade:
    return container.facade


def get_health_service(container: Container = Depends(get_container)) -> HealthService:
    return container.health_service


def get_outbox_dispatcher(container: Container = Depends(get_container)) -> OutboxDispatcher:
    return container.outbox_dispatcher


def get_lifecycle_service(
    container: Container = Depends(get_container),
) -> CredentialLifecycleService:
    return container.lifecycle_service


def get_pass_issuance_service(
    container: Container = Depends(get_container),
) -> PassIssuanceService:
    return container.pass_issuance_service


def get_credential_verifier(container: Container = Depends(get_container)) -> CredentialVerifier:
    return container.credential_verifier


async def verify_qstash_signature(
    request: Request, container: Container = Depends(get_container)
) -> None:
    await container.qstash_verifier.verify(request)

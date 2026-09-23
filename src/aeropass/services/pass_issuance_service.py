"""US4 — Issuance (and automatic renewal) of the dynamic QR (plan.md, "Pass issuance")."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from aeropass.domain.credential.builder import CredencialAccesoBuilder
from aeropass.domain.credential.credential import MOTIVO_RENOVACION, CredencialAcceso
from aeropass.domain.credential.signing import CredentialSigner
from aeropass.domain.errors import (
    AlmacenamientoNoDisponible,
    DocumentoVencidoParaPase,
    IdentidadNoActiva,
    LimiteEmisionExcedido,
)
from aeropass.observability.hooks import audited, traced
from aeropass.ports.auth import AuthenticatedUser
from aeropass.ports.clock import Clock
from aeropass.ports.flight_catalog import FlightCatalog
from aeropass.ports.rate_limiter import RateLimiter
from aeropass.ports.repositories import UnitOfWork
from aeropass.ports.token_store import TokenStore, TokenStoreUnavailable
from aeropass.services.credential_lifecycle_service import CredentialLifecycleService

RENEWAL_MARGIN_SECONDS = 5
MOTIVO_TOKEN_NO_REGISTRADO = "ALMACENAMIENTO_NO_DISPONIBLE"


@dataclass(frozen=True)
class IssuedPass:
    credencial: CredencialAcceso
    token: str
    renovar_en_segundos: int


class PassIssuanceService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        tokens: TokenStore,
        rate_limiter: RateLimiter,
        flights: FlightCatalog,
        signer: CredentialSigner,
        lifecycle: CredentialLifecycleService,
        clock: Clock,
        ttl_seconds: int,
    ) -> None:
        self._uow = uow_factory
        self._tokens = tokens
        self._rate = rate_limiter
        self._flights = flights
        self._signer = signer
        self._lifecycle = lifecycle
        self._clock = clock
        self._ttl = ttl_seconds

    @traced("passes.issue")
    @audited("credential.issue")
    async def issue(self, user: AuthenticatedUser, codigo_vuelo: str) -> IssuedPass:
        codigo = await self._flights.validate(codigo_vuelo)

        async with self._uow() as uow:
            pasajero = await uow.passengers.get_by_clerk_user(user.clerk_user_id)
        if pasajero is None:
            raise IdentidadNoActiva()

        limite = await self._rate.hit(str(pasajero.id))
        if not limite.allowed:
            raise LimiteEmisionExcedido(retry_after=limite.retry_after)

        anterior_a_olvidar: uuid.UUID | None = None
        async with self._uow() as uow:
            locked = await uow.passengers.get_for_update(pasajero.id)  # serializes renewals
            identidad = await uow.identities.get_active(pasajero.id)
            if locked is None or identidad is None:
                raise IdentidadNoActiva()
            now = self._clock.now()
            if not locked.documento.vigente_en(now.date()):
                raise DocumentoVencidoParaPase()

            anterior = await uow.credentials.get_live_for_update(pasajero.id, codigo)
            if anterior is not None:
                if anterior.refresh_expiry(now):
                    await uow.credentials.save(anterior)
                elif (await self._lifecycle.revoke(uow, anterior, MOTIVO_RENOVACION)).aceptada:
                    anterior_a_olvidar = anterior.id

            credencial, token = (
                CredencialAccesoBuilder()
                .para_pasajero(pasajero.id)
                .con_identidad(identidad.id)
                .para_vuelo(codigo)
                .con_ttl(self._ttl)
                .firmado_con(self._signer)
                .emitido_en(now)
                .build()
            )
            await uow.credentials.add(credencial)
            try:
                await self._tokens.register(credencial.id, self._ttl)
            except TokenStoreUnavailable as exc:
                credencial.revocar(MOTIVO_TOKEN_NO_REGISTRADO, now)
                await uow.credentials.save(credencial)
                await uow.commit()
                raise AlmacenamientoNoDisponible(retry_after=1) from exc
            credencial.activar(now)
            await uow.credentials.save(credencial)
            await uow.commit()

        if anterior_a_olvidar is not None:  # only after the commit (analysis finding D2)
            await self._lifecycle.forget_token(anterior_a_olvidar)
        return IssuedPass(
            credencial=credencial,
            token=token,
            renovar_en_segundos=self._ttl - RENEWAL_MARGIN_SECONDS,
        )

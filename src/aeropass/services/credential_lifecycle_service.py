"""Lifecycle of CredencialAcceso after emission: expiry, revocation and consumption.

``consume`` is the extension point for the checkpoint module (contracts/extension-points.md §1).
Every call commits its transition row, accepted or rejected (FR-019): transitions never raise.
Redis side effects happen after the commit; Postgres is the source of truth.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from enum import StrEnum

from aeropass.domain.credential.credential import CredencialAcceso, Transicion, TransitionResult
from aeropass.domain.enums import EstadoCredencial
from aeropass.domain.errors import CredencialNoEncontrada
from aeropass.observability.hooks import audited, traced
from aeropass.observability.telemetry_catalog import CREDENTIAL_CONSUME
from aeropass.ports.clock import Clock
from aeropass.ports.repositories import UnitOfWork
from aeropass.ports.token_store import TokenStore, TokenStoreUnavailable

logger = logging.getLogger(__name__)


class ConsumeResult(StrEnum):
    """Maps 1:1 to ``datos.motivo`` of ``validacion.fallida`` v1 for every value but CONSUMIDA."""

    CONSUMIDA = "CONSUMIDA"
    YA_CONSUMIDA = "YA_CONSUMIDA"  # RN-06
    EXPIRADA = "EXPIRADA"
    REVOCADA = "REVOCADA"
    NO_ENCONTRADA = "NO_ENCONTRADA"


_REJECTION: dict[EstadoCredencial, ConsumeResult] = {
    EstadoCredencial.CONSUMIDA: ConsumeResult.YA_CONSUMIDA,
    EstadoCredencial.EXPIRADA: ConsumeResult.EXPIRADA,
    EstadoCredencial.REVOCADA: ConsumeResult.REVOCADA,
    # EMITIDA is activated in the same transaction that creates it, so it is never observable;
    # treat it as not usable.
    EstadoCredencial.EMITIDA: ConsumeResult.REVOCADA,
}


class CredentialLifecycleService:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], tokens: TokenStore, clock: Clock
    ) -> None:
        self._uow = uow_factory
        self._tokens = tokens
        self._clock = clock

    @traced("credential.consume")
    @audited(CREDENTIAL_CONSUME)
    async def consume(self, jti: uuid.UUID, *, actor: str) -> ConsumeResult:
        now = self._clock.now()
        async with self._uow() as uow:
            credencial = await uow.credentials.get_for_update(jti)
            if credencial is None:
                return ConsumeResult.NO_ENCONTRADA
            credencial.refresh_expiry(now)
            result = credencial.consumir(now, actor=actor)
            await uow.credentials.save(credencial)
            await uow.commit()

        if not result.aceptada:
            return _REJECTION[result.estado_actual]
        try:
            await self._tokens.consume(jti)
        except TokenStoreUnavailable:
            logger.warning("consumed %s in Postgres but Redis was not updated", jti)
        return ConsumeResult.CONSUMIDA

    async def revoke(
        self, uow: UnitOfWork, credencial: CredencialAcceso, motivo: str
    ) -> TransitionResult:
        """Revoke inside the caller's transaction. The caller must call ``forget_token`` after
        committing when the result is accepted."""
        result = credencial.revocar(motivo, self._clock.now())
        await uow.credentials.save(credencial)
        return result

    @traced("credential.revoke")
    async def revoke_by_id(self, jti: uuid.UUID, *, motivo: str) -> TransitionResult:
        async with self._uow() as uow:
            credencial = await uow.credentials.get_for_update(jti)
            if credencial is None:
                raise CredencialNoEncontrada()
            credencial.refresh_expiry(self._clock.now())
            result = await self.revoke(uow, credencial, motivo)
            await uow.commit()
        if result.aceptada:
            await self.forget_token(jti)
        return result

    async def forget_token(self, jti: uuid.UUID) -> None:
        """Post-commit, best effort: if Redis fails the key still expires within 60 s."""
        try:
            await self._tokens.revoke(jti)
        except TokenStoreUnavailable:
            logger.warning("could not delete qr:%s; it will expire on its own", jti)

    @traced("credential.detail")
    async def detail_for_owner(
        self, jti: uuid.UUID, pasajero_id: uuid.UUID
    ) -> tuple[CredencialAcceso, list[Transicion]]:
        """Applies lazy expiry, then returns the credential and its full history."""
        async with self._uow() as uow:
            credencial = await uow.credentials.get_for_update(jti)
            if credencial is None or credencial.pasajero_id != pasajero_id:
                raise CredencialNoEncontrada()
            if credencial.refresh_expiry(self._clock.now()):
                await uow.credentials.save(credencial)
            historial = await uow.credentials.history(jti)
            await uow.commit()
        return credencial, historial

    @traced("credential.sweep_expired")
    async def sweep_expired(self, limit: int = 500) -> int:
        now = self._clock.now()
        async with self._uow() as uow:
            vencidas = await uow.credentials.expired_live_for_update(now, limit)
            for credencial in vencidas:
                credencial.expirar(now)
                await uow.credentials.save(credencial)
            await uow.commit()
        return len(vencidas)

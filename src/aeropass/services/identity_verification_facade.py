"""Facade: ``verify_and_create_identity(pasajero, doc, selfie)`` (constitution, Facade pattern).

Hides the pipeline document → liveness → face match → identity (plan.md, "Selfie
verification"). No DB transaction is open while talking to Blob or the biometric provider.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from aeropass.domain.enums import ResultadoIntento
from aeropass.domain.errors import AlmacenamientoNoDisponible, PasajeroNoRegistrado
from aeropass.domain.ids import new_id
from aeropass.domain.images import ImageInput, validate_image
from aeropass.domain.passenger import DocumentoRegistrado, Pasajero
from aeropass.domain.verification import IntentoVerificacion
from aeropass.observability.hooks import audited, traced
from aeropass.observability.telemetry_catalog import IDENTITY_VERIFICATION
from aeropass.ports.biometric_provider import SafeBiometricProvider
from aeropass.ports.media_storage import MediaStorage, MediaUnavailable
from aeropass.ports.repositories import UnitOfWork
from aeropass.services.biometric_verification_service import BiometricVerificationService
from aeropass.services.identity_service import IdentityService
from aeropass.services.outbox_dispatcher import OutboxDispatcher

STORAGE_RETRY_AFTER_SECONDS = 5


@dataclass(frozen=True)
class VerificationOutcome:
    intento: IntentoVerificacion
    pasajero: Pasajero
    identidad_id: uuid.UUID | None = None


class IdentityVerificationFacade:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        media: MediaStorage,
        provider: SafeBiometricProvider,
        verification: BiometricVerificationService,
        identity: IdentityService,
        dispatcher: OutboxDispatcher,
    ) -> None:
        self._uow = uow_factory
        self._media = media
        self._provider = provider
        self._verification = verification
        self._identity = identity
        self._dispatcher = dispatcher

    @traced("facade.verify_and_create_identity")
    @audited(IDENTITY_VERIFICATION)
    async def verify_and_create_identity(
        self, pasajero: Pasajero, doc: DocumentoRegistrado, selfie: ImageInput
    ) -> VerificationOutcome:
        imagen = validate_image(selfie.data, selfie.content_type)
        pasajero.assert_can_verify()  # cheap early rejection, re-checked under lock below

        intento_id = new_id()
        try:
            stored, referencia = await asyncio.gather(
                self._media.put_private(f"selfies/{pasajero.id}/{intento_id}", imagen),
                self._media.get(doc.foto.pathname),
            )
        except MediaUnavailable as exc:
            raise AlmacenamientoNoDisponible(retry_after=STORAGE_RETRY_AFTER_SECONDS) from exc

        result = await self._provider.evaluate(imagen, referencia)

        async with self._uow() as uow:
            locked = await uow.passengers.get_for_update(pasajero.id)
            if locked is None:
                raise PasajeroNoRegistrado()
            # State may have changed while we talked to external services (concurrent selfie):
            # this raises and the uploaded selfie stays orphaned but identifiable by intento_id.
            locked.assert_can_verify()
            intento = await self._verification.record_attempt(
                uow,
                locked,
                intento_id=intento_id,
                selfie=stored,
                result=result,
                proveedor=self._provider.name,
            )
            identidad_id = None
            event = None
            if intento.resultado is ResultadoIntento.EXITOSO:
                identidad, event = await self._identity.create_for_success(uow, locked, intento)
                identidad_id = identidad.id
            await uow.commit()

        if event is not None:  # after commit, never blocking the response on failure
            await self._dispatcher.publish_now([event.id])
        return VerificationOutcome(
            intento=intento, pasajero=locked.con_identidad(identidad_id), identidad_id=identidad_id
        )

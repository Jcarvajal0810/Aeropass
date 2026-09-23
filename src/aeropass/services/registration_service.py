"""US1 — Document registration (plan.md, "Registration" flow)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from aeropass.domain.enums import TipoDocumento
from aeropass.domain.errors import (
    AlmacenamientoNoDisponible,
    CuentaYaRegistrada,
    DatosInvalidos,
    DocumentoVencido,
    DocumentoYaRegistrado,
    PasajeroNoRegistrado,
)
from aeropass.domain.ids import new_id
from aeropass.domain.images import validate_image
from aeropass.domain.passenger import DatosDocumento, Pasajero
from aeropass.observability.hooks import traced
from aeropass.ports.auth import AuthenticatedUser
from aeropass.ports.clock import Clock
from aeropass.ports.media_storage import MediaStorage, MediaUnavailable
from aeropass.ports.repositories import UniqueViolation, UnitOfWork

STORAGE_RETRY_AFTER_SECONDS = 5


class RegistrationService:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], media: MediaStorage, clock: Clock
    ) -> None:
        self._uow = uow_factory
        self._media = media
        self._clock = clock

    @traced("registration.register")
    async def register(
        self,
        user: AuthenticatedUser,
        *,
        nombre_completo: str | None,
        tipo_documento: TipoDocumento | str | None,
        numero_documento: str | None,
        fecha_vencimiento: date | None,
        foto: bytes | None,
        foto_content_type: str | None,
    ) -> tuple[Pasajero, bool]:
        """Returns ``(pasajero, created)``. ``created`` is False for an identical resubmission."""
        datos = self._validate(
            nombre_completo, tipo_documento, numero_documento, fecha_vencimiento, foto
        )
        imagen = validate_image(foto or b"", foto_content_type)

        async with self._uow() as uow:
            propio = await uow.passengers.get_by_clerk_user(user.clerk_user_id)
            if propio is not None:
                if propio.documento.es_mismo(datos):
                    return await self._with_identity(uow, propio), False
                raise CuentaYaRegistrada()
            if await uow.passengers.get_by_document(datos.tipo, datos.numero):
                raise DocumentoYaRegistrado()

        pasajero_id = new_id()
        try:  # outside any transaction: never hold a DB connection during external I/O
            foto_ref = await self._media.put_private(f"documentos/{pasajero_id}/rostro", imagen)
        except MediaUnavailable as exc:
            raise AlmacenamientoNoDisponible(retry_after=STORAGE_RETRY_AFTER_SECONDS) from exc

        pasajero = Pasajero.registrar(
            id=pasajero_id,
            clerk_user_id=user.clerk_user_id,
            datos=datos,
            foto=foto_ref,
            ahora=self._clock.now(),
        )
        async with self._uow() as uow:
            try:
                await uow.passengers.add(pasajero)
                await uow.commit()
            except UniqueViolation as exc:
                # A concurrent request won; the uploaded photo stays orphaned (identifiable
                # by its pasajero_id prefix) and the caller gets the same answer it would have.
                await uow.rollback()
                return await self._resolve_race(user, datos, exc)
        return pasajero, True

    async def _resolve_race(
        self, user: AuthenticatedUser, datos: DatosDocumento, exc: UniqueViolation
    ) -> tuple[Pasajero, bool]:
        async with self._uow() as uow:
            propio = await uow.passengers.get_by_clerk_user(user.clerk_user_id)
            if propio is not None and propio.documento.es_mismo(datos):
                return await self._with_identity(uow, propio), False
        if propio is not None:
            raise CuentaYaRegistrada() from exc
        raise DocumentoYaRegistrado() from exc

    @staticmethod
    async def _with_identity(uow: UnitOfWork, pasajero: Pasajero) -> Pasajero:
        identidad = await uow.identities.get_active(pasajero.id)
        return pasajero.con_identidad(identidad.id if identidad else None)

    def _validate(
        self,
        nombre_completo: str | None,
        tipo_documento: TipoDocumento | str | None,
        numero_documento: str | None,
        fecha_vencimiento: date | None,
        foto: bytes | None,
    ) -> DatosDocumento:
        """Missing/invalid fields (photo included) are reported before document expiry."""
        faltantes = [] if foto else ["foto_documento"]
        try:
            datos = DatosDocumento.validar(
                nombre_completo=nombre_completo,
                tipo_documento=tipo_documento,
                numero_documento=numero_documento,
                fecha_vencimiento=fecha_vencimiento,
                hoy=self._clock.today(),
            )
        except DatosInvalidos as exc:
            campos = sorted(set((exc.detalles or {}).get("campos", [])) | set(faltantes))
            raise DatosInvalidos(detalles={"campos": campos}) from exc
        except DocumentoVencido:
            if faltantes:
                raise DatosInvalidos(detalles={"campos": faltantes}) from None
            raise
        if faltantes:
            raise DatosInvalidos(detalles={"campos": faltantes})
        return datos

    @traced("registration.get_mine")
    async def get_mine(self, user: AuthenticatedUser) -> Pasajero:
        async with self._uow() as uow:
            pasajero = await uow.passengers.get_by_clerk_user(user.clerk_user_id)
            if pasajero is None:
                raise PasajeroNoRegistrado()
            return await self._with_identity(uow, pasajero)

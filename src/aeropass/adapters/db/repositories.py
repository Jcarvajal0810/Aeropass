"""SQLAlchemy implementations of the repository ports (row ↔ domain mapping lives here)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aeropass.adapters.db.orm import (
    CredencialAccesoRow,
    IdentidadDigitalRow,
    IntentoVerificacionRow,
    OutboxEventoRow,
    PasajeroRow,
    TransicionCredencialRow,
)
from aeropass.domain.credential.credential import CredencialAcceso, Transicion
from aeropass.domain.enums import (
    EstadoCredencial,
    EstadoEvento,
    EstadoIdentidad,
    EstadoPasajero,
    ResultadoIntento,
    TipoDocumento,
)
from aeropass.domain.events import DomainEvent, OutboxEntry
from aeropass.domain.identity import IdentidadDigital
from aeropass.domain.images import StoredMedia
from aeropass.domain.passenger import DocumentoRegistrado, Pasajero
from aeropass.domain.verification import IntentoVerificacion
from aeropass.ports.repositories import UniqueViolation


def _constraint_name(exc: IntegrityError) -> str:
    orig = getattr(exc.orig, "__cause__", None) or exc.orig
    return getattr(orig, "constraint_name", None) or str(exc.orig)


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        raise UniqueViolation(_constraint_name(exc)) from exc


# --- Pasajero ---------------------------------------------------------------------------


def _to_passenger(row: PasajeroRow) -> Pasajero:
    return Pasajero(
        id=row.id,
        clerk_user_id=row.clerk_user_id,
        documento=DocumentoRegistrado(
            nombre_completo=row.nombre_completo,
            tipo=row.tipo_documento,
            numero=row.numero_documento,
            fecha_vencimiento=row.fecha_vencimiento_documento,
            foto=StoredMedia(
                url=row.foto_documento_blob_url, pathname=row.foto_documento_blob_pathname
            ),
        ),
        estado=row.estado,
        intentos_fallidos=row.intentos_fallidos,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _copy_passenger(p: Pasajero, row: PasajeroRow) -> None:
    row.clerk_user_id = p.clerk_user_id
    row.nombre_completo = p.documento.nombre_completo
    row.tipo_documento = p.documento.tipo
    row.numero_documento = p.documento.numero
    row.fecha_vencimiento_documento = p.documento.fecha_vencimiento
    row.foto_documento_blob_pathname = p.documento.foto.pathname
    row.foto_documento_blob_url = p.documento.foto.url
    row.estado = p.estado
    row.intentos_fallidos = p.intentos_fallidos
    row.created_at = p.created_at  # type: ignore[assignment]
    row.updated_at = p.updated_at  # type: ignore[assignment]


class SqlPassengerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, stmt) -> Pasajero | None:  # type: ignore[no-untyped-def]
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_passenger(row) if row else None

    async def get(self, id: uuid.UUID) -> Pasajero | None:
        return await self._one(select(PasajeroRow).where(PasajeroRow.id == id))

    async def get_by_clerk_user(self, clerk_user_id: str) -> Pasajero | None:
        return await self._one(
            select(PasajeroRow).where(PasajeroRow.clerk_user_id == clerk_user_id)
        )

    async def get_by_document(self, tipo: TipoDocumento, numero: str) -> Pasajero | None:
        return await self._one(
            select(PasajeroRow).where(
                PasajeroRow.tipo_documento == tipo, PasajeroRow.numero_documento == numero
            )
        )

    async def get_for_update(self, id: uuid.UUID) -> Pasajero | None:
        return await self._one(
            select(PasajeroRow)
            .where(PasajeroRow.id == id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def add(self, pasajero: Pasajero) -> None:
        row = PasajeroRow(id=pasajero.id)
        _copy_passenger(pasajero, row)
        self._session.add(row)
        await _flush(self._session)

    async def save(self, pasajero: Pasajero) -> None:
        row = await self._session.get(PasajeroRow, pasajero.id)
        assert row is not None, "save() of a passenger that was never added"
        _copy_passenger(pasajero, row)
        await _flush(self._session)

    async def list_by_estado(self, estado: EstadoPasajero, limit: int = 100) -> list[Pasajero]:
        rows = (
            await self._session.execute(
                select(PasajeroRow)
                .where(PasajeroRow.estado == estado)
                .order_by(PasajeroRow.updated_at)
                .limit(limit)
            )
        ).scalars()
        return [_to_passenger(r) for r in rows]


# --- IntentoVerificacion ----------------------------------------------------------------


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 3)))


class SqlVerificationAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, intento: IntentoVerificacion) -> None:
        self._session.add(
            IntentoVerificacionRow(
                id=intento.id,
                pasajero_id=intento.pasajero_id,
                selfie_blob_pathname=intento.selfie.pathname,
                selfie_blob_url=intento.selfie.url,
                resultado=intento.resultado,
                motivo_fallo=intento.motivo_fallo,
                score_liveness=_dec(intento.score_liveness),
                score_comparacion=_dec(intento.score_comparacion),
                umbral_liveness=_dec(intento.umbrales.liveness),
                umbral_comparacion=_dec(intento.umbrales.comparacion),
                proveedor=intento.proveedor,
                created_at=intento.created_at,
            )
        )
        await _flush(self._session)

    async def count_failed(self, pasajero_id: uuid.UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(IntentoVerificacionRow)
                .where(
                    IntentoVerificacionRow.pasajero_id == pasajero_id,
                    IntentoVerificacionRow.resultado == ResultadoIntento.FALLIDO,
                )
            )
        ).scalar_one()


# --- IdentidadDigital -------------------------------------------------------------------


def _to_identity(row: IdentidadDigitalRow) -> IdentidadDigital:
    return IdentidadDigital(
        id=row.id,
        pasajero_id=row.pasajero_id,
        intento_origen_id=row.intento_origen_id,
        estado=row.estado,
        created_at=row.created_at,
        revocada_at=row.revocada_at,
    )


class SqlIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, identidad: IdentidadDigital) -> None:
        self._session.add(
            IdentidadDigitalRow(
                id=identidad.id,
                pasajero_id=identidad.pasajero_id,
                intento_origen_id=identidad.intento_origen_id,
                estado=identidad.estado,
                revocada_at=identidad.revocada_at,
                created_at=identidad.created_at,
            )
        )
        await _flush(self._session)

    async def get_active(self, pasajero_id: uuid.UUID) -> IdentidadDigital | None:
        row = (
            await self._session.execute(
                select(IdentidadDigitalRow).where(
                    IdentidadDigitalRow.pasajero_id == pasajero_id,
                    IdentidadDigitalRow.estado == EstadoIdentidad.ACTIVA,
                )
            )
        ).scalar_one_or_none()
        return _to_identity(row) if row else None


# --- Outbox -----------------------------------------------------------------------------


def _to_entry(row: OutboxEventoRow) -> OutboxEntry:
    payload = row.payload
    return OutboxEntry(
        event=DomainEvent(
            id=row.id,
            tipo=row.tipo,
            version=row.version,
            ocurrido_at=datetime.fromisoformat(payload["ocurrido_at"].replace("Z", "+00:00")),
            datos=payload["datos"],
        ),
        estado=row.estado,
        intentos=row.intentos,
        proximo_intento_at=row.proximo_intento_at,
        ultimo_error=row.ultimo_error,
        entregado_at=row.entregado_at,
    )


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: OutboxEntry) -> None:
        self._session.add(
            OutboxEventoRow(
                id=entry.event.id,
                tipo=entry.event.tipo,
                version=entry.event.version,
                payload=entry.event.to_payload(),
                estado=entry.estado,
                intentos=entry.intentos,
                proximo_intento_at=entry.proximo_intento_at,
                ultimo_error=entry.ultimo_error,
                created_at=entry.event.ocurrido_at,
                entregado_at=entry.entregado_at,
            )
        )
        await _flush(self._session)

    async def claim_due(
        self,
        now: datetime,
        lease_until: datetime,
        limit: int,
        ids: Sequence[uuid.UUID] | None = None,
    ) -> list[OutboxEntry]:
        stmt = (
            select(OutboxEventoRow)
            .where(
                OutboxEventoRow.estado == EstadoEvento.PENDIENTE,
                OutboxEventoRow.proximo_intento_at <= now,
            )
            .order_by(OutboxEventoRow.proximo_intento_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if ids is not None:
            stmt = stmt.where(OutboxEventoRow.id.in_(list(ids)))
        rows = list((await self._session.execute(stmt)).scalars())
        entries = [_to_entry(r) for r in rows]
        for row in rows:
            row.proximo_intento_at = lease_until
        await self._session.flush()
        return entries

    async def save(self, entry: OutboxEntry) -> None:
        row = await self._session.get(OutboxEventoRow, entry.event.id)
        assert row is not None
        row.estado = entry.estado
        row.intentos = entry.intentos
        row.proximo_intento_at = entry.proximo_intento_at
        row.ultimo_error = entry.ultimo_error
        row.entregado_at = entry.entregado_at
        await self._session.flush()

    async def count_pending(self) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(OutboxEventoRow)
                .where(OutboxEventoRow.estado == EstadoEvento.PENDIENTE)
            )
        ).scalar_one()


# --- CredencialAcceso -------------------------------------------------------------------

_LIVE = (EstadoCredencial.EMITIDA, EstadoCredencial.ACTIVA)


def _to_credential(row: CredencialAccesoRow) -> CredencialAcceso:
    return CredencialAcceso(
        id=row.id,
        pasajero_id=row.pasajero_id,
        identidad_id=row.identidad_id,
        codigo_vuelo=row.codigo_vuelo,
        permisos=tuple(row.permisos),
        firma=row.firma,
        kid=row.kid,
        emitida_at=row.emitida_at,
        expira_at=row.expira_at,
        estado=row.estado,
        updated_at=row.updated_at,
    )


class SqlCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _flush_transitions(self, credencial: CredencialAcceso) -> None:
        for t in credencial.pending_transitions:
            self._session.add(
                TransicionCredencialRow(
                    credencial_id=credencial.id,
                    estado_anterior=t.estado_anterior,
                    estado_solicitado=t.estado_solicitado,
                    aceptada=t.aceptada,
                    motivo=t.motivo,
                    actor=t.actor,
                    created_at=t.created_at,
                )
            )
        credencial.pending_transitions.clear()

    async def add(self, credencial: CredencialAcceso) -> None:
        self._session.add(
            CredencialAccesoRow(
                id=credencial.id,
                pasajero_id=credencial.pasajero_id,
                identidad_id=credencial.identidad_id,
                codigo_vuelo=credencial.codigo_vuelo,
                permisos=list(credencial.permisos),
                firma=credencial.firma,
                kid=credencial.kid,
                emitida_at=credencial.emitida_at,
                expira_at=credencial.expira_at,
                estado=credencial.estado,
                updated_at=credencial.updated_at,
            )
        )
        await _flush(self._session)  # the row must exist before its history (FK)
        self._flush_transitions(credencial)
        await _flush(self._session)

    async def save(self, credencial: CredencialAcceso) -> None:
        row = await self._session.get(CredencialAccesoRow, credencial.id)
        assert row is not None, "save() of a credential that was never added"
        row.estado = credencial.estado
        row.updated_at = credencial.updated_at
        self._flush_transitions(credencial)
        await _flush(self._session)

    async def _locked(self, stmt) -> list[CredencialAcceso]:  # type: ignore[no-untyped-def]
        rows = (
            await self._session.execute(stmt.execution_options(populate_existing=True))
        ).scalars()
        return [_to_credential(r) for r in rows]

    async def get_for_update(self, id: uuid.UUID) -> CredencialAcceso | None:
        found = await self._locked(
            select(CredencialAccesoRow).where(CredencialAccesoRow.id == id).with_for_update()
        )
        return found[0] if found else None

    async def get_live_for_update(
        self, pasajero_id: uuid.UUID, codigo_vuelo: str
    ) -> CredencialAcceso | None:
        found = await self._locked(
            select(CredencialAccesoRow)
            .where(
                CredencialAccesoRow.pasajero_id == pasajero_id,
                CredencialAccesoRow.codigo_vuelo == codigo_vuelo,
                CredencialAccesoRow.estado.in_(_LIVE),
            )
            .with_for_update()
        )
        return found[0] if found else None

    async def expired_live_for_update(self, now: datetime, limit: int) -> list[CredencialAcceso]:
        return await self._locked(
            select(CredencialAccesoRow)
            .where(CredencialAccesoRow.estado.in_(_LIVE), CredencialAccesoRow.expira_at <= now)
            .order_by(CredencialAccesoRow.expira_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def history(self, id: uuid.UUID) -> list[Transicion]:
        rows = (
            await self._session.execute(
                select(TransicionCredencialRow)
                .where(TransicionCredencialRow.credencial_id == id)
                .order_by(TransicionCredencialRow.id)
            )
        ).scalars()
        return [
            Transicion(
                estado_anterior=r.estado_anterior,
                estado_solicitado=r.estado_solicitado,
                aceptada=r.aceptada,
                motivo=r.motivo,
                actor=r.actor,
                created_at=r.created_at,
            )
            for r in rows
        ]

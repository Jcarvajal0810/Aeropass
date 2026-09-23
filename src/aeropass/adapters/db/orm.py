"""SQLAlchemy mappings (data-model.md). Tables are created by Alembic migrations only."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from aeropass.domain.enums import (
    EstadoCredencial,
    EstadoEvento,
    EstadoIdentidad,
    EstadoPasajero,
    MotivoFallo,
    ResultadoIntento,
    TipoDocumento,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pg_enum(enum_cls: type[StrEnum], name: str) -> ENUM:
    return ENUM(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda cls: [member.value for member in cls],
    )


PG_ENUMS: dict[str, type[StrEnum]] = {
    "tipo_documento": TipoDocumento,
    "estado_pasajero": EstadoPasajero,
    "resultado_intento": ResultadoIntento,
    "motivo_fallo": MotivoFallo,
    "estado_identidad": EstadoIdentidad,
    "estado_credencial": EstadoCredencial,
    "estado_evento": EstadoEvento,
}

TipoDocumentoType = pg_enum(TipoDocumento, "tipo_documento")
EstadoPasajeroType = pg_enum(EstadoPasajero, "estado_pasajero")
ResultadoIntentoType = pg_enum(ResultadoIntento, "resultado_intento")
MotivoFalloType = pg_enum(MotivoFallo, "motivo_fallo")
EstadoIdentidadType = pg_enum(EstadoIdentidad, "estado_identidad")
EstadoCredencialType = pg_enum(EstadoCredencial, "estado_credencial")
EstadoEventoType = pg_enum(EstadoEvento, "estado_evento")

TZ = DateTime(timezone=True)


class PasajeroRow(Base):
    __tablename__ = "pasajeros"
    __table_args__ = (
        UniqueConstraint("tipo_documento", "numero_documento", name="uq_pasajeros_documento"),
        CheckConstraint("intentos_fallidos BETWEEN 0 AND 3", name="intentos_fallidos_rango"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    clerk_user_id: Mapped[str] = mapped_column(Text, unique=True)
    nombre_completo: Mapped[str] = mapped_column(Text)
    tipo_documento: Mapped[TipoDocumento] = mapped_column(TipoDocumentoType)
    numero_documento: Mapped[str] = mapped_column(Text)
    fecha_vencimiento_documento: Mapped[date] = mapped_column(Date)
    foto_documento_blob_pathname: Mapped[str] = mapped_column(Text)
    foto_documento_blob_url: Mapped[str] = mapped_column(Text)
    estado: Mapped[EstadoPasajero] = mapped_column(EstadoPasajeroType)
    intentos_fallidos: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class IntentoVerificacionRow(Base):
    __tablename__ = "intentos_verificacion"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    pasajero_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pasajeros.id"), index=True
    )
    selfie_blob_pathname: Mapped[str | None] = mapped_column(Text)
    selfie_blob_url: Mapped[str | None] = mapped_column(Text)
    resultado: Mapped[ResultadoIntento] = mapped_column(ResultadoIntentoType)
    motivo_fallo: Mapped[MotivoFallo | None] = mapped_column(MotivoFalloType)
    score_liveness: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    score_comparacion: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    umbral_liveness: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    umbral_comparacion: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    proveedor: Mapped[str] = mapped_column(Text)
    imagen_eliminada_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ)


class IdentidadDigitalRow(Base):
    __tablename__ = "identidades_digitales"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    pasajero_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pasajeros.id"))
    intento_origen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intentos_verificacion.id"), unique=True
    )
    estado: Mapped[EstadoIdentidad] = mapped_column(EstadoIdentidadType)
    revocada_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ)


class OutboxEventoRow(Base):
    __tablename__ = "outbox_eventos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tipo: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estado: Mapped[EstadoEvento] = mapped_column(EstadoEventoType)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    proximo_intento_at: Mapped[datetime] = mapped_column(TZ)
    ultimo_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ)
    entregado_at: Mapped[datetime | None] = mapped_column(TZ)


class CredencialAccesoRow(Base):
    __tablename__ = "credenciales_acceso"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    pasajero_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pasajeros.id"))
    identidad_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identidades_digitales.id")
    )
    codigo_vuelo: Mapped[str] = mapped_column(Text)
    permisos: Mapped[list[str]] = mapped_column(ARRAY(Text))
    firma: Mapped[str] = mapped_column(Text)
    kid: Mapped[str] = mapped_column(Text)
    emitida_at: Mapped[datetime] = mapped_column(TZ)
    expira_at: Mapped[datetime] = mapped_column(TZ)
    estado: Mapped[EstadoCredencial] = mapped_column(EstadoCredencialType)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class TransicionCredencialRow(Base):
    __tablename__ = "transiciones_credencial"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    credencial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credenciales_acceso.id")
    )
    estado_anterior: Mapped[EstadoCredencial | None] = mapped_column(EstadoCredencialType)
    estado_solicitado: Mapped[EstadoCredencial] = mapped_column(EstadoCredencialType)
    aceptada: Mapped[bool] = mapped_column(Boolean)
    motivo: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ)

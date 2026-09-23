"""Request/response models (contracts/openapi.yaml)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from aeropass.domain.credential.credential import CredencialAcceso, Transicion
from aeropass.domain.enums import (
    EstadoCredencial,
    EstadoPasajero,
    MotivoFallo,
    ResultadoIntento,
    TipoDocumento,
)
from aeropass.domain.passenger import Pasajero
from aeropass.services.identity_verification_facade import VerificationOutcome
from aeropass.services.pass_issuance_service import IssuedPass


class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str
    detalles: dict[str, Any] | None = None


class PasajeroResponse(BaseModel):
    id: uuid.UUID
    nombre_completo: str
    tipo_documento: TipoDocumento
    numero_documento_enmascarado: str
    fecha_vencimiento: date
    estado: EstadoPasajero
    intentos_fallidos: int
    identidad_id: uuid.UUID | None = None

    @classmethod
    def from_domain(cls, p: Pasajero) -> PasajeroResponse:
        return cls(
            id=p.id,
            nombre_completo=p.documento.nombre_completo,
            tipo_documento=p.documento.tipo,
            numero_documento_enmascarado=p.documento.numero_enmascarado,
            fecha_vencimiento=p.documento.fecha_vencimiento,
            estado=p.estado,
            intentos_fallidos=p.intentos_fallidos,
            identidad_id=p.identidad_id,
        )


REINTENTAR_EN_SEGUNDOS = 30  # matches the circuit breaker's open period


class ResultadoVerificacionResponse(BaseModel):
    intento_id: uuid.UUID
    resultado: ResultadoIntento
    motivo_fallo: MotivoFallo | None
    score_liveness: float | None
    score_comparacion: float | None
    estado_pasajero: EstadoPasajero
    intentos_restantes: int
    identidad_id: uuid.UUID | None = None
    reintentar_en_segundos: int | None = None

    @classmethod
    def from_outcome(cls, outcome: VerificationOutcome) -> ResultadoVerificacionResponse:
        intento = outcome.intento
        return cls(
            intento_id=intento.id,
            resultado=intento.resultado,
            motivo_fallo=intento.motivo_fallo,
            score_liveness=intento.score_liveness,
            score_comparacion=intento.score_comparacion,
            estado_pasajero=outcome.pasajero.estado,
            intentos_restantes=outcome.pasajero.intentos_restantes,
            identidad_id=outcome.identidad_id,
            reintentar_en_segundos=(
                REINTENTAR_EN_SEGUNDOS
                if intento.resultado is ResultadoIntento.NO_CONCLUYENTE
                else None
            ),
        )


class EmitirPaseRequest(BaseModel):
    codigo_vuelo: str = Field(max_length=16)


class PaseResponse(BaseModel):
    credencial_id: uuid.UUID
    token: str
    codigo_vuelo: str
    permisos: list[str]
    estado: EstadoCredencial
    emitida_at: datetime
    expira_at: datetime
    renovar_en_segundos: int

    @classmethod
    def from_issued(cls, issued: IssuedPass) -> PaseResponse:
        c = issued.credencial
        return cls(
            credencial_id=c.id,
            token=issued.token,
            codigo_vuelo=c.codigo_vuelo,
            permisos=list(c.permisos),
            estado=c.estado,
            emitida_at=c.emitida_at,
            expira_at=c.expira_at,
            renovar_en_segundos=issued.renovar_en_segundos,
        )


class TransicionResponse(BaseModel):
    estado_anterior: EstadoCredencial | None
    estado_solicitado: EstadoCredencial
    aceptada: bool
    motivo: str | None
    created_at: datetime


class DetallePaseResponse(BaseModel):
    credencial_id: uuid.UUID
    codigo_vuelo: str
    estado: EstadoCredencial
    emitida_at: datetime
    expira_at: datetime
    historial: list[TransicionResponse]

    @classmethod
    def from_domain(cls, c: CredencialAcceso, historial: list[Transicion]) -> DetallePaseResponse:
        return cls(
            credencial_id=c.id,
            codigo_vuelo=c.codigo_vuelo,
            estado=c.estado,
            emitida_at=c.emitida_at,
            expira_at=c.expira_at,
            historial=[
                TransicionResponse(
                    estado_anterior=t.estado_anterior,
                    estado_solicitado=t.estado_solicitado,
                    aceptada=t.aceptada,
                    motivo=t.motivo,
                    created_at=t.created_at,
                )
                for t in historial
            ],
        )

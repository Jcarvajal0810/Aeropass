"""Enumerations shared by domain, persistence and API (single source, data-model.md)."""

from enum import StrEnum


class TipoDocumento(StrEnum):
    CC = "CC"
    CE = "CE"
    PASAPORTE = "PASAPORTE"


class EstadoPasajero(StrEnum):
    PENDIENTE_VERIFICACION = "PENDIENTE_VERIFICACION"
    VERIFICADO = "VERIFICADO"
    REQUIERE_REVISION_MANUAL = "REQUIERE_REVISION_MANUAL"


class ResultadoIntento(StrEnum):
    EXITOSO = "EXITOSO"
    FALLIDO = "FALLIDO"
    NO_CONCLUYENTE = "NO_CONCLUYENTE"


class MotivoFallo(StrEnum):
    LIVENESS = "LIVENESS"
    COMPARACION = "COMPARACION"


class EstadoIdentidad(StrEnum):
    ACTIVA = "ACTIVA"
    REVOCADA = "REVOCADA"


class EstadoCredencial(StrEnum):
    EMITIDA = "EMITIDA"
    ACTIVA = "ACTIVA"
    CONSUMIDA = "CONSUMIDA"
    EXPIRADA = "EXPIRADA"
    REVOCADA = "REVOCADA"


class EstadoEvento(StrEnum):
    PENDIENTE = "PENDIENTE"
    ENTREGADO = "ENTREGADO"

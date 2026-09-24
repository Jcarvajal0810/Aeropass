"""Pasajero aggregate and document rules (single source for FR-001..FR-004, FR-008)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from aeropass.domain.enums import EstadoPasajero, ResultadoIntento, TipoDocumento
from aeropass.domain.errors import DatosInvalidos, DocumentoVencido, EstadoNoPermiteVerificacion
from aeropass.domain.images import StoredMedia

MAX_INTENTOS_FALLIDOS = 3
_ESTADOS_FINALES = frozenset({EstadoPasajero.VERIFICADO, EstadoPasajero.REQUIERE_REVISION_MANUAL})

_SEPARADORES = re.compile(r"[\s.\-]")
_NUMERO_VALIDO = re.compile(r"^[A-Z0-9]{4,20}$")


def normalizar_numero(numero: str) -> str:
    return _SEPARADORES.sub("", numero or "").upper()


def enmascarar(numero: str) -> str:
    return "*" * max(len(numero) - 4, 0) + numero[-4:]


@dataclass(frozen=True)
class DatosDocumento:
    """Validated text data read from the document."""

    nombre_completo: str
    tipo: TipoDocumento
    numero: str
    fecha_vencimiento: date

    @classmethod
    def validar(
        cls,
        *,
        nombre_completo: str | None,
        tipo_documento: TipoDocumento | str | None,
        numero_documento: str | None,
        fecha_vencimiento: date | None,
        hoy: date,
    ) -> DatosDocumento:
        invalidos: list[str] = []
        nombre = " ".join((nombre_completo or "").split())
        if not 2 <= len(nombre) <= 200:
            invalidos.append("nombre_completo")
        try:
            tipo = TipoDocumento(tipo_documento) if tipo_documento else None
        except ValueError:
            tipo = None
        if tipo is None:
            invalidos.append("tipo_documento")
        numero = normalizar_numero(numero_documento or "")
        if not _NUMERO_VALIDO.match(numero):
            invalidos.append("numero_documento")
        if fecha_vencimiento is None:
            invalidos.append("fecha_vencimiento")
        if invalidos:
            raise DatosInvalidos(detalles={"campos": sorted(invalidos)})
        assert tipo is not None and fecha_vencimiento is not None
        if fecha_vencimiento < hoy:
            raise DocumentoVencido()
        return cls(nombre, tipo, numero, fecha_vencimiento)

    @property
    def numero_enmascarado(self) -> str:
        return enmascarar(self.numero)


@dataclass(frozen=True)
class DocumentoRegistrado:
    """Document data plus the private reference to the face photo (FR-001a)."""

    nombre_completo: str
    tipo: TipoDocumento
    numero: str
    fecha_vencimiento: date
    foto: StoredMedia

    @classmethod
    def desde(cls, datos: DatosDocumento, foto: StoredMedia) -> DocumentoRegistrado:
        return cls(datos.nombre_completo, datos.tipo, datos.numero, datos.fecha_vencimiento, foto)

    def es_mismo(self, datos: DatosDocumento) -> bool:
        return self.tipo == datos.tipo and self.numero == datos.numero

    def vigente_en(self, dia: date) -> bool:
        return self.fecha_vencimiento >= dia

    @property
    def numero_enmascarado(self) -> str:
        return enmascarar(self.numero)


@dataclass
class Pasajero:
    id: uuid.UUID
    clerk_user_id: str
    documento: DocumentoRegistrado
    estado: EstadoPasajero = EstadoPasajero.PENDIENTE_VERIFICACION
    intentos_fallidos: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    identidad_id: uuid.UUID | None = field(default=None, compare=False)

    @classmethod
    def registrar(
        cls,
        *,
        id: uuid.UUID,
        clerk_user_id: str,
        datos: DatosDocumento,
        foto: StoredMedia,
        ahora: datetime,
    ) -> Pasajero:
        return cls(
            id=id,
            clerk_user_id=clerk_user_id,
            documento=DocumentoRegistrado.desde(datos, foto),
            created_at=ahora,
            updated_at=ahora,
        )

    def con_identidad(self, identidad_id: uuid.UUID | None) -> Pasajero:
        return replace(self, identidad_id=identidad_id)

    # --- biometric verification (FR-008) -------------------------------------------------
    def assert_can_verify(self) -> None:
        if self.estado is not EstadoPasajero.PENDIENTE_VERIFICACION:
            raise EstadoNoPermiteVerificacion()

    def apply_outcome(self, resultado: ResultadoIntento, ahora: datetime) -> None:
        self.assert_can_verify()
        if resultado is ResultadoIntento.EXITOSO:
            self.estado = EstadoPasajero.VERIFICADO
        elif resultado is ResultadoIntento.FALLIDO:
            self.intentos_fallidos += 1
            if self.intentos_fallidos >= MAX_INTENTOS_FALLIDOS:
                self.estado = EstadoPasajero.REQUIERE_REVISION_MANUAL
        # NO_CONCLUYENTE: provider unavailable, not the passenger's fault — does not count
        self.updated_at = ahora

    @property
    def estado_final(self) -> EstadoPasajero | None:
        """The state if verification has concluded for this passenger, else ``None``.

        Single source of "final state" (KR A1.2 self-service rate, spec 002): a passenger reaches
        it once, since ``apply_outcome`` only runs from ``PENDIENTE_VERIFICACION``.
        """
        if self.estado in _ESTADOS_FINALES:
            return self.estado
        return None

    @property
    def intentos_restantes(self) -> int:
        if self.estado is EstadoPasajero.REQUIERE_REVISION_MANUAL:
            return 0
        return max(MAX_INTENTOS_FALLIDOS - self.intentos_fallidos, 0)

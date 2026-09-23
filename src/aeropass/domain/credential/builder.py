"""Builder for CredencialAcceso (constitution: Builder).

A credential cannot be built unless every rule holds: passenger, identity, valid flight code,
non-empty permissions, TTL within 30–60 s, a signer and an emission instant.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Self

from aeropass.domain.credential.credential import CredencialAcceso
from aeropass.domain.credential.signing import CredentialSigner
from aeropass.domain.errors import DatosInvalidos
from aeropass.domain.flight import normalizar_codigo_vuelo
from aeropass.domain.ids import new_id

TTL_MIN_SECONDS = 30
TTL_MAX_SECONDS = 60
PERMISOS_POR_DEFECTO = ("embarque",)


class CredencialAccesoBuilder:
    def __init__(self) -> None:
        self._pasajero_id: uuid.UUID | None = None
        self._identidad_id: uuid.UUID | None = None
        self._codigo_vuelo: str | None = None
        self._permisos: tuple[str, ...] = PERMISOS_POR_DEFECTO
        self._ttl: int | None = None
        self._signer: CredentialSigner | None = None
        self._emitida_at: datetime | None = None

    def para_pasajero(self, pasajero_id: uuid.UUID) -> Self:
        self._pasajero_id = pasajero_id
        return self

    def con_identidad(self, identidad_id: uuid.UUID) -> Self:
        self._identidad_id = identidad_id
        return self

    def para_vuelo(self, codigo_vuelo: str) -> Self:
        self._codigo_vuelo = codigo_vuelo
        return self

    def con_permisos(self, permisos: Iterable[str]) -> Self:
        self._permisos = tuple(permisos)
        return self

    def con_ttl(self, seconds: int) -> Self:
        self._ttl = seconds
        return self

    def firmado_con(self, signer: CredentialSigner) -> Self:
        self._signer = signer
        return self

    def emitido_en(self, instante: datetime) -> Self:
        self._emitida_at = instante
        return self

    def build(self) -> tuple[CredencialAcceso, str]:
        """Returns the EMITIDA credential and its signed token (the QR content)."""
        faltantes = [
            name
            for name, value in (
                ("pasajero", self._pasajero_id),
                ("identidad", self._identidad_id),
                ("codigo_vuelo", self._codigo_vuelo),
                ("ttl", self._ttl),
                ("firma", self._signer),
                ("emitida_at", self._emitida_at),
            )
            if value is None
        ]
        if faltantes:
            raise DatosInvalidos("Credencial incompleta", detalles={"campos": faltantes})
        assert self._pasajero_id and self._identidad_id and self._signer and self._emitida_at
        assert self._ttl is not None
        if not TTL_MIN_SECONDS <= self._ttl <= TTL_MAX_SECONDS:
            raise DatosInvalidos("QR validity must be between 30 and 60 seconds")
        if not self._permisos or not all(p.strip() for p in self._permisos):
            raise DatosInvalidos(
                "The credential requires permissions", detalles={"campos": ["permisos"]}
            )
        codigo_vuelo = normalizar_codigo_vuelo(self._codigo_vuelo)

        jti = new_id()
        expira_at = self._emitida_at + timedelta(seconds=self._ttl)
        token = self._signer.sign(
            {
                "jti": str(jti),
                "sub": str(self._pasajero_id),
                "flt": codigo_vuelo,
                "perms": list(self._permisos),
                "iat": int(self._emitida_at.timestamp()),
                "exp": int(expira_at.timestamp()),
            }
        )
        credencial = CredencialAcceso.emitir(
            id=jti,
            pasajero_id=self._pasajero_id,
            identidad_id=self._identidad_id,
            codigo_vuelo=codigo_vuelo,
            permisos=self._permisos,
            firma=self._signer.signature_of(token),
            kid=self._signer.kid,
            emitida_at=self._emitida_at,
            expira_at=expira_at,
        )
        return credencial, token

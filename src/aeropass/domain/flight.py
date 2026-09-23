"""Flight code format — single source for the builder and ``FormatOnlyFlightCatalog``."""

import re

from aeropass.domain.errors import DatosInvalidos

_CODIGO_VUELO = re.compile(r"^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$")


def normalizar_codigo_vuelo(codigo: str | None) -> str:
    normalizado = (codigo or "").strip().upper()
    if not _CODIGO_VUELO.match(normalizado):
        raise DatosInvalidos("Código de vuelo inválido", detalles={"campos": ["codigo_vuelo"]})
    return normalizado

from aeropass.domain.flight import normalizar_codigo_vuelo


class FormatOnlyFlightCatalog:
    """Validates only the format. Replace with an ``AirlineApiAdapter`` (via the container) when
    the flights/GDS integration exists — no service changes needed."""

    async def validate(self, codigo_vuelo: str) -> str:
        return normalizar_codigo_vuelo(codigo_vuelo)

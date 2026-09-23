from typing import Protocol


class FlightCatalog(Protocol):
    """Extension point for the airline/GDS integration (out of scope for this feature)."""

    async def validate(self, codigo_vuelo: str) -> str:
        """Return the normalized flight code or raise ``DatosInvalidos``."""
        ...

from starlette.requests import Request

from aeropass.domain.errors import NoAutenticado


class FakeQStashVerifier:
    """Accepts ``Upstash-Signature: test`` only."""

    async def verify(self, request: Request) -> None:
        if request.headers.get("upstash-signature") != "test":
            raise NoAutenticado("Firma de QStash inválida")

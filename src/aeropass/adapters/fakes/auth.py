from starlette.requests import Request

from aeropass.domain.errors import NoAutenticado
from aeropass.observability.hooks import audited
from aeropass.observability.telemetry_catalog import AUTH_AUTHENTICATE
from aeropass.ports.auth import AuthenticatedUser

_PREFIX = "Bearer test:"


class FakeAuth:
    """Local/test authenticator: ``Authorization: Bearer test:<user_id>``."""

    @audited(AUTH_AUTHENTICATE)
    async def authenticate(self, request: Request) -> AuthenticatedUser:
        header = request.headers.get("authorization", "")
        if not header.startswith(_PREFIX) or len(header) == len(_PREFIX):
            raise NoAutenticado()
        return AuthenticatedUser(clerk_user_id=header[len(_PREFIX) :])

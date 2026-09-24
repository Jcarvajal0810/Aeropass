"""Clerk session-token verification (research R9). No API Gateway: every router depends on it."""

from clerk_backend_api.security import authenticate_request
from clerk_backend_api.security.types import AuthenticateRequestOptions
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from aeropass.domain.errors import NoAutenticado
from aeropass.observability.hooks import audited
from aeropass.observability.telemetry_catalog import AUTH_AUTHENTICATE
from aeropass.ports.auth import AuthenticatedUser


class ClerkAuthenticator:
    def __init__(self, secret_key: str, authorized_parties: list[str] | None = None) -> None:
        self._options = AuthenticateRequestOptions(
            secret_key=secret_key,
            authorized_parties=authorized_parties or None,
        )

    @audited(AUTH_AUTHENTICATE)
    async def authenticate(self, request: Request) -> AuthenticatedUser:
        if not request.headers.get("authorization"):
            raise NoAutenticado()
        # The SDK verifies the JWT against Clerk's JWKS (cached per process) synchronously.
        state = await run_in_threadpool(authenticate_request, request, self._options)
        if not state.is_signed_in or not state.payload or not state.payload.get("sub"):
            raise NoAutenticado()
        return AuthenticatedUser(clerk_user_id=str(state.payload["sub"]))

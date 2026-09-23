from dataclasses import dataclass
from typing import Protocol

from starlette.requests import Request


@dataclass(frozen=True)
class AuthenticatedUser:
    clerk_user_id: str


class Authenticator(Protocol):
    async def authenticate(self, request: Request) -> AuthenticatedUser:
        """Return the caller or raise ``NoAutenticado``."""
        ...

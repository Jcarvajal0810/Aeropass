import uuid
from typing import Protocol


class TokenStoreUnavailable(Exception):
    pass


class TokenStore(Protocol):
    """Single-use token registry (FR-017, RN-06). Redis is a fast path, Postgres is the truth."""

    async def register(self, jti: uuid.UUID, ttl_seconds: int) -> None:
        """Mark the token usable for ``ttl_seconds``. Raises ``TokenStoreUnavailable``."""
        ...

    async def revoke(self, jti: uuid.UUID) -> None: ...

    async def consume(self, jti: uuid.UUID) -> bool:
        """Atomically flip ACTIVA → CONSUMIDA. False if missing, expired or already consumed."""
        ...

    async def is_active(self, jti: uuid.UUID) -> bool: ...

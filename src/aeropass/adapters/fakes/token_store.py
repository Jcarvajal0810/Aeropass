import asyncio
import uuid
from datetime import datetime, timedelta

from aeropass.ports.clock import Clock
from aeropass.ports.token_store import TokenStoreUnavailable


class FakeTokenStore:
    """In-memory ``qr:{jti}`` with TTLs driven by the injected clock."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._values: dict[uuid.UUID, tuple[str, datetime, int]] = {}
        self.fail_next_register = False
        self.fail_next_revoke = False

    def _live(self, jti: uuid.UUID) -> str | None:
        entry = self._values.get(jti)
        if entry is None or self._clock.now() >= entry[1]:
            return None
        return entry[0]

    def ttl(self, jti: uuid.UUID) -> int | None:
        entry = self._values.get(jti)
        return entry[2] if entry else None

    async def register(self, jti: uuid.UUID, ttl_seconds: int) -> None:
        if self.fail_next_register:
            self.fail_next_register = False
            raise TokenStoreUnavailable("simulated outage")
        async with self._lock:
            if self._live(jti) is not None:
                raise TokenStoreUnavailable("token already registered")
            expires = self._clock.now() + timedelta(seconds=ttl_seconds)
            self._values[jti] = ("ACTIVA", expires, ttl_seconds)

    async def revoke(self, jti: uuid.UUID) -> None:
        if self.fail_next_revoke:
            self.fail_next_revoke = False
            raise TokenStoreUnavailable("simulated outage")
        self._values.pop(jti, None)

    async def consume(self, jti: uuid.UUID) -> bool:
        async with self._lock:
            if self._live(jti) != "ACTIVA":
                return False
            _, expires, ttl = self._values[jti]
            self._values[jti] = ("CONSUMIDA", expires, ttl)
            return True

    async def is_active(self, jti: uuid.UUID) -> bool:
        return self._live(jti) == "ACTIVA"

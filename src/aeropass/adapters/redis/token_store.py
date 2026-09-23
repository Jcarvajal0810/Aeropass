"""Single-use QR tokens in Upstash Redis at ``qr:{jti}`` (research R6)."""

import uuid

from upstash_redis.asyncio import Redis

from aeropass.observability.hooks import traced
from aeropass.ports.token_store import TokenStoreUnavailable

ACTIVA = "ACTIVA"
CONSUMIDA = "CONSUMIDA"

# Atomic ACTIVA → CONSUMIDA keeping the TTL, so "consumed" and "expired" stay distinguishable.
_CONSUME = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
  return 1
end
return 0
"""


def _key(jti: uuid.UUID) -> str:
    return f"qr:{jti}"


class UpstashTokenStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @traced("redis.token.register")
    async def register(self, jti: uuid.UUID, ttl_seconds: int) -> None:
        try:
            ok = await self._redis.set(_key(jti), ACTIVA, nx=True, ex=ttl_seconds)
        except Exception as exc:
            raise TokenStoreUnavailable(type(exc).__name__) from exc
        if not ok:
            raise TokenStoreUnavailable("token already registered")

    @traced("redis.token.revoke")
    async def revoke(self, jti: uuid.UUID) -> None:
        try:
            await self._redis.delete(_key(jti))
        except Exception as exc:
            raise TokenStoreUnavailable(type(exc).__name__) from exc

    @traced("redis.token.consume")
    async def consume(self, jti: uuid.UUID) -> bool:
        try:
            result = await self._redis.eval(_CONSUME, keys=[_key(jti)], args=[ACTIVA, CONSUMIDA])
        except Exception as exc:
            raise TokenStoreUnavailable(type(exc).__name__) from exc
        return int(result or 0) == 1

    @traced("redis.token.is_active")
    async def is_active(self, jti: uuid.UUID) -> bool:
        try:
            return await self._redis.get(_key(jti)) == ACTIVA
        except Exception as exc:
            raise TokenStoreUnavailable(type(exc).__name__) from exc

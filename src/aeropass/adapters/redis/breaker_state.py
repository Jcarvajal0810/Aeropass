from upstash_redis.asyncio import Redis

from aeropass.adapters.resilience.circuit_breaker import BreakerState

_TTL_SECONDS = 600


class RedisBreakerStateStore:
    """Breaker state shared by every serverless instance, at ``cb:{name}``."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, name: str) -> BreakerState:
        data = await self._redis.hgetall(f"cb:{name}")
        return BreakerState.from_mapping(data or None)

    async def save(self, name: str, state: BreakerState) -> None:
        key = f"cb:{name}"
        await self._redis.hset(key, values=state.to_mapping())
        await self._redis.expire(key, _TTL_SECONDS)

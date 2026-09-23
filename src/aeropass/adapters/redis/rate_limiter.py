"""Sliding-window rate limit for pass emission: 30 per 60 s per passenger (FR-020a)."""

import math
import time

from upstash_ratelimit.asyncio import Ratelimit, SlidingWindow
from upstash_redis.asyncio import Redis

from aeropass.ports.rate_limiter import RateLimitResult

MAX_EMISIONES = 30
VENTANA_SEGUNDOS = 60


class UpstashRateLimiter:
    def __init__(self, redis: Redis, prefix: str = "rl:passes") -> None:
        self._limiter = Ratelimit(
            redis=redis,
            limiter=SlidingWindow(max_requests=MAX_EMISIONES, window=VENTANA_SEGUNDOS),
            prefix=prefix,
        )

    async def hit(self, key: str) -> RateLimitResult:
        try:
            response = await self._limiter.limit(key)
        except Exception:
            # Fail open: rate limiting protects capacity, it must not block boarding.
            return RateLimitResult(allowed=True)
        if response.allowed:
            return RateLimitResult(allowed=True)
        retry_after = max(1, math.ceil(response.reset - time.time()))
        return RateLimitResult(allowed=False, retry_after=retry_after)

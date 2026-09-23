import math
from collections import defaultdict, deque
from datetime import datetime

from aeropass.adapters.redis.rate_limiter import MAX_EMISIONES, VENTANA_SEGUNDOS
from aeropass.ports.clock import Clock
from aeropass.ports.rate_limiter import RateLimitResult


class FakeRateLimiter:
    """Sliding window with the same limits as production, driven by the injected clock."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)

    async def hit(self, key: str) -> RateLimitResult:
        now = self._clock.now()
        hits = self._hits[key]
        while hits and (now - hits[0]).total_seconds() >= VENTANA_SEGUNDOS:
            hits.popleft()
        if len(hits) >= MAX_EMISIONES:
            wait = VENTANA_SEGUNDOS - (now - hits[0]).total_seconds()
            return RateLimitResult(allowed=False, retry_after=max(1, math.ceil(wait)))
        hits.append(now)
        return RateLimitResult(allowed=True)

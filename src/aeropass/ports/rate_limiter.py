from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class RateLimiter(Protocol):
    async def hit(self, key: str) -> RateLimitResult: ...

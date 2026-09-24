"""Dependencies checked by ``GET /health`` (spec 002, contracts/health-endpoint.md)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from upstash_redis.asyncio import Redis


class DatabaseHealthCheck:
    name = "database"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def check(self) -> None:
        async with self._session_factory() as session:
            await session.execute(text("SELECT 1"))


class RedisHealthCheck:
    name = "redis"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self) -> None:
        await self._redis.ping()

"""Singleton Upstash Redis client (REST over HTTP: no sockets to keep alive in serverless)."""

from functools import cache

from upstash_redis.asyncio import Redis

from aeropass.config import get_settings


@cache
def get_redis() -> Redis:
    settings = get_settings()
    return Redis(
        url=settings.upstash_redis_rest_url,
        token=settings.upstash_redis_rest_token,
        rest_retries=0,
        allow_telemetry=False,
    )

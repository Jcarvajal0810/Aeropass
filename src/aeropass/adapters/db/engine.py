"""Singleton async engine for Neon Postgres (research R1).

One engine per process: reused while the serverless instance is warm. Neon's pooled endpoint
runs PgBouncer in transaction mode, so prepared-statement caches must be disabled.
"""

from functools import cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from aeropass.config import get_settings

_UNSUPPORTED_BY_ASYNCPG = {"sslmode", "channel_binding"}


def normalize_async_url(url: str) -> str:
    """Accept plain Neon URLs (``postgresql://…?sslmode=require``) and adapt them to asyncpg."""
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    if "sslmode" in query and "ssl" not in query:
        query["ssl"] = query["sslmode"]
    for key in _UNSUPPORTED_BY_ASYNCPG:
        query.pop(key, None)
    query.setdefault("prepared_statement_cache_size", "0")
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        normalize_async_url(url),
        pool_size=1,
        max_overflow=4,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"statement_cache_size": 0},
    )


@cache
def get_engine() -> AsyncEngine:
    return build_engine(get_settings().database_url)


@cache
def get_session_factory() -> async_sessionmaker:
    return async_sessionmaker(get_engine(), expire_on_commit=False)

"""Shared fixtures.

Integration tests need a real Postgres: ``TEST_DATABASE_URL`` if set, otherwise an embedded
server via ``pgserver`` (dev dependency, data in ``.pgdata/``). Without either, they are skipped.
External services always use the in-memory doubles (``AEROPASS_ADAPTERS=fake``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aeropass.adapters.clock import FakeClock
from aeropass.adapters.db.engine import build_engine, normalize_async_url
from aeropass.adapters.fakes.images import make_image
from aeropass.api.deps import Container
from aeropass.config import Settings
from aeropass.main import create_app
from tests.support.sentry_capture import sentry_capture  # noqa: F401 - shared fixture (spec 002)

ROOT = Path(__file__).resolve().parent.parent
TEST_DB = "aeropass_test"


def _embedded_postgres_url() -> str | None:
    try:
        import pgserver  # type: ignore[import-not-found]
    except ImportError:
        return None
    server = pgserver.get_server(ROOT / ".pgdata", cleanup_mode=None)
    if TEST_DB not in server.psql("select datname from pg_database;"):
        server.psql(f"CREATE DATABASE {TEST_DB};")
    return server.get_uri().rsplit("/", 1)[0] + f"/{TEST_DB}"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or _embedded_postgres_url()
    if not url:
        pytest.skip("integration tests need TEST_DATABASE_URL or pgserver")
    url = normalize_async_url(url)

    async def reset_schema() -> None:
        dsn = url.replace("postgresql+asyncpg://", "postgresql://").split("?")[0]
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await conn.close()

    asyncio.run(reset_schema())
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture(scope="session")
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    """Clean tables before each integration test."""
    async with session_factory() as session:
        tables = (
            await session.execute(
                text(
                    "select tablename from pg_tables where schemaname='public' "
                    "and tablename <> 'alembic_version'"
                )
            )
        ).scalars()
        names = ", ".join(tables)
        if names:
            await session.execute(text(f"TRUNCATE {names} CASCADE"))
            await session.commit()
    yield


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        aeropass_adapters="fake",
        biometric_provider="mock",
        qr_ttl_seconds=45,
        biometric_timeout_seconds=0.3,
    )


@pytest.fixture
def container(
    settings: Settings, fake_clock: FakeClock, request: pytest.FixtureRequest
) -> Container:
    c = Container(settings)
    c.clock = fake_clock  # type: ignore[misc]
    if "db" in request.fixturenames:
        c.session_factory = request.getfixturevalue("session_factory")  # type: ignore[misc]
    return c


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers() -> Callable[[str], dict[str, str]]:
    return lambda user_id: {"Authorization": f"Bearer test:{user_id}"}


@pytest.fixture
def image() -> Callable[..., bytes]:
    return make_image

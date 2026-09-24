"""Async Alembic environment. Uses the DIRECT (non-pooled) Neon URL."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from aeropass.adapters.db.engine import normalize_async_url
from aeropass.adapters.db.orm import Base
from aeropass.config import get_settings

config = context.config
if config.config_file_name is not None:
    # Keep the app's loggers alive when migrations run in-process (the test fixture does):
    # the default would silence every "aeropass.*" logger created before this call.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    override = config.attributes.get("url") or os.environ.get("ALEMBIC_DATABASE_URL")
    return normalize_async_url(override or get_settings().migrations_url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), connect_args={"statement_cache_size": 0})
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

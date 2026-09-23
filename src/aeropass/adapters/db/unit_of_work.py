from __future__ import annotations

from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aeropass.adapters.db.repositories import (
    SqlCredentialRepository,
    SqlIdentityRepository,
    SqlOutboxRepository,
    SqlPassengerRepository,
    SqlVerificationAttemptRepository,
)


class SqlAlchemyUnitOfWork:
    """One ``AsyncSession`` per business transaction; rolls back unless committed."""

    session: AsyncSession
    passengers: SqlPassengerRepository
    attempts: SqlVerificationAttemptRepository
    identities: SqlIdentityRepository
    outbox: SqlOutboxRepository
    credentials: SqlCredentialRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self._bind_repositories(self.session)
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    def _bind_repositories(self, session: AsyncSession) -> None:
        """Attach one repository per aggregate."""
        self.passengers = SqlPassengerRepository(session)
        self.attempts = SqlVerificationAttemptRepository(session)
        self.identities = SqlIdentityRepository(session)
        self.outbox = SqlOutboxRepository(session)
        self.credentials = SqlCredentialRepository(session)

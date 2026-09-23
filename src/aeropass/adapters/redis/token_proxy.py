"""``RedisVerificationProxy`` (constitution: Proxy).

Answers "is this QR still usable?" from Redis before anything touches Postgres, keeping the
checkpoint's hot path fast. A missing key means NOT usable (safe default); only a positive answer
needs the authoritative Postgres check that ``CredentialLifecycleService.consume`` performs.
"""

import uuid

from aeropass.ports.token_store import TokenStore, TokenStoreUnavailable


class RedisVerificationProxy:
    def __init__(self, token_store: TokenStore) -> None:
        self._tokens = token_store

    async def may_be_usable(self, jti: uuid.UUID) -> bool | None:
        """False → reject without touching Postgres. True → confirm in Postgres.
        None → Redis unavailable: fall back to Postgres (degraded, still correct)."""
        try:
            return await self._tokens.is_active(jti)
        except TokenStoreUnavailable:
            return None

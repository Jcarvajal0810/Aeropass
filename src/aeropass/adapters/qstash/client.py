"""Singleton QStash client."""

from functools import cache

from qstash import AsyncQStash

from aeropass.config import get_settings


@cache
def get_qstash() -> AsyncQStash:
    return AsyncQStash(get_settings().qstash_token)

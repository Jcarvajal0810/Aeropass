"""Singleton Vercel Blob client (one per process)."""

from functools import cache

from vercel.blob import AsyncBlobClient

from aeropass.config import get_settings


@cache
def get_blob_client() -> AsyncBlobClient:
    return AsyncBlobClient(token=get_settings().blob_read_write_token or None)

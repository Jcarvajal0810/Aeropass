"""Adapter: Vercel Blob private store behind the ``MediaStorage`` port (research R2)."""

import asyncio

from vercel.blob import AsyncBlobClient, BlobError

from aeropass.domain.images import ImageInput, extension_for, sniff_content_type
from aeropass.observability.hooks import traced
from aeropass.ports.media_storage import MediaUnavailable, StoredMedia


class VercelBlobStorage:
    def __init__(self, client: AsyncBlobClient, timeout_seconds: float) -> None:
        self._client = client
        self._timeout = timeout_seconds

    @traced("blob.put_private")
    async def put_private(self, pathname: str, image: ImageInput) -> StoredMedia:
        try:
            result = await asyncio.wait_for(
                self._client.put(
                    f"{pathname}.{extension_for(image.content_type)}",
                    image.data,
                    access="private",
                    content_type=image.content_type,
                    add_random_suffix=True,
                ),
                timeout=self._timeout,
            )
        except (BlobError, TimeoutError, OSError) as exc:
            raise MediaUnavailable(type(exc).__name__) from exc
        return StoredMedia(url=result.url, pathname=result.pathname)

    @traced("blob.get")
    async def get(self, pathname: str) -> ImageInput:
        try:
            result = await asyncio.wait_for(
                self._client.get(pathname, access="private", use_cache=False),
                timeout=self._timeout,
            )
        except (BlobError, TimeoutError, OSError) as exc:
            raise MediaUnavailable(type(exc).__name__) from exc
        data = bytes(result.content)
        content_type = result.content_type or sniff_content_type(data) or "application/octet-stream"
        return ImageInput(data=data, content_type=content_type)

    @traced("blob.delete")
    async def delete(self, pathname: str) -> None:
        try:
            await asyncio.wait_for(self._client.delete(pathname), timeout=self._timeout)
        except (BlobError, TimeoutError, OSError) as exc:
            raise MediaUnavailable(type(exc).__name__) from exc

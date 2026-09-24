"""Adapter: Vercel Blob private store behind the ``MediaStorage`` port (research R2)."""

import asyncio
import logging
import time

from vercel.blob import AsyncBlobClient, BlobError

from aeropass.domain.images import ImageInput, extension_for, sniff_content_type
from aeropass.observability.hooks import traced
from aeropass.ports.media_storage import MediaUnavailable, StoredMedia

logger = logging.getLogger(__name__)


class VercelBlobStorage:
    def __init__(self, client: AsyncBlobClient, timeout_seconds: float) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def _unavailable(
        self, operation: str, exc: Exception, started: float, size: int | None = None
    ) -> MediaUnavailable:
        # Operation, size and elapsed time only: never the pathname or image bytes.
        logger.warning(
            "blob %s failed: %s after %d ms (bytes=%s, timeout=%.1fs)",
            operation,
            type(exc).__name__,
            (time.monotonic() - started) * 1000,
            size if size is not None else "-",
            self._timeout,
        )
        return MediaUnavailable(type(exc).__name__)

    @traced("blob.put_private")
    async def put_private(self, pathname: str, image: ImageInput) -> StoredMedia:
        started = time.monotonic()
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
            raise self._unavailable("put_private", exc, started, len(image.data)) from exc
        return StoredMedia(url=result.url, pathname=result.pathname)

    @traced("blob.get")
    async def get(self, pathname: str) -> ImageInput:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._client.get(pathname, access="private", use_cache=False),
                timeout=self._timeout,
            )
        except (BlobError, TimeoutError, OSError) as exc:
            raise self._unavailable("get", exc, started) from exc
        data = bytes(result.content)
        content_type = result.content_type or sniff_content_type(data) or "application/octet-stream"
        return ImageInput(data=data, content_type=content_type)

    @traced("blob.delete")
    async def delete(self, pathname: str) -> None:
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._client.delete(pathname), timeout=self._timeout)
        except (BlobError, TimeoutError, OSError) as exc:
            raise self._unavailable("delete", exc, started) from exc

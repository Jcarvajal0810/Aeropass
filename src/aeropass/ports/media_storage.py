from typing import Protocol

from aeropass.domain.images import ImageInput, StoredMedia

__all__ = ["MediaStorage", "MediaUnavailable", "StoredMedia"]


class MediaUnavailable(Exception):
    """The media store could not complete the operation (network, 5xx, timeout)."""


class MediaStorage(Protocol):
    async def put_private(self, pathname: str, image: ImageInput) -> StoredMedia:
        """Store privately; the final pathname gets a random suffix."""
        ...

    async def get(self, pathname: str) -> ImageInput: ...

    async def delete(self, pathname: str) -> None: ...

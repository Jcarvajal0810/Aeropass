import secrets

from aeropass.domain.images import ImageInput, extension_for
from aeropass.ports.media_storage import MediaUnavailable, StoredMedia


class InMemoryMediaStorage:
    """Private media store double. ``fail_next_*`` simulate an outage for one call."""

    def __init__(self) -> None:
        self.objects: dict[str, ImageInput] = {}
        self.fail_next_put = False
        self.fail_next_get = False

    async def put_private(self, pathname: str, image: ImageInput) -> StoredMedia:
        if self.fail_next_put:
            self.fail_next_put = False
            raise MediaUnavailable("simulated put failure")
        final = f"{pathname}-{secrets.token_hex(8)}.{extension_for(image.content_type)}"
        self.objects[final] = image
        return StoredMedia(url=f"https://fake.private.blob.local/{final}", pathname=final)

    async def get(self, pathname: str) -> ImageInput:
        if self.fail_next_get:
            self.fail_next_get = False
            raise MediaUnavailable("simulated get failure")
        try:
            return self.objects[pathname]
        except KeyError as exc:
            raise MediaUnavailable("not found") from exc

    async def delete(self, pathname: str) -> None:
        self.objects.pop(pathname, None)

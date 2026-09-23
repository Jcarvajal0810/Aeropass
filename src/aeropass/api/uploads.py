"""Upload guards shared by the multipart endpoints."""

from fastapi import Request, UploadFile

from aeropass.domain.errors import ImagenDemasiadoGrande
from aeropass.domain.images import MAX_IMAGE_BYTES

# Vercel Functions reject bodies above 4.5 MB; refuse early, before reading the stream.
MAX_BODY_BYTES = 4_500_000


def reject_oversized_body(request: Request) -> None:
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise ImagenDemasiadoGrande()


async def read_upload(upload: UploadFile | None) -> tuple[bytes | None, str | None]:
    if upload is None:
        return None, None
    data = await upload.read(MAX_IMAGE_BYTES + 1)  # one extra byte is enough to detect "too big"
    return (data or None), upload.content_type

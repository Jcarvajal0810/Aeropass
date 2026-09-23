"""Image input rules shared by the selfie and the document photo (single source, DRY)."""

from dataclasses import dataclass

from aeropass.domain.errors import FormatoNoAdmitido, ImagenDemasiadoGrande

MAX_IMAGE_BYTES = 4 * 1024 * 1024

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    content_type: str

    def __repr__(self) -> str:  # never leak image bytes into logs or tracebacks
        return f"ImageInput(content_type={self.content_type!r}, size={len(self.data)})"


@dataclass(frozen=True)
class StoredMedia:
    """Reference to an object in the private media store. Never the content itself."""

    url: str
    pathname: str


def sniff_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(data: bytes, declared_content_type: str | None) -> ImageInput:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImagenDemasiadoGrande()
    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if declared == "image/jpg":
        declared = "image/jpeg"
    actual = sniff_content_type(data)
    if declared not in _EXTENSIONS or actual != declared:
        raise FormatoNoAdmitido()
    return ImageInput(data=data, content_type=actual)


def extension_for(content_type: str) -> str:
    return _EXTENSIONS[content_type]

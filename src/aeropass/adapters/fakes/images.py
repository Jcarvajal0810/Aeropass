"""Tiny images carrying a ``MOCK:<marker>`` that drives ``MockBiometricAdapter``.

The bytes are a structurally valid JPEG/PNG container (correct signature, a comment chunk with
the marker, end marker). They are not meant to be decoded, only to pass format validation.
"""

import struct
import zlib

MARKER_PREFIX = b"MOCK:"


def make_image(marker: str | None = "ok", fmt: str = "JPEG") -> bytes:
    text = (MARKER_PREFIX + marker.encode("ascii")) if marker else b"no-marker"
    if fmt.upper() == "PNG":
        return _png(text)
    return _jpeg(text)


def _jpeg(text: bytes) -> bytes:
    comment = b"\xff\xfe" + struct.pack(">H", len(text) + 2) + text
    return b"\xff\xd8" + comment + b"\xff\xd9"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _png(text: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"tEXt", b"Comment\x00" + text)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def read_marker(data: bytes) -> str | None:
    start = data.find(MARKER_PREFIX)
    if start < 0:
        return None
    end = start + len(MARKER_PREFIX)
    stop = end
    while stop < len(data) and data[stop : stop + 1].isalnum():  # bytes.isalnum is ASCII-only
        stop += 1
    return data[end:stop].decode("ascii") or None

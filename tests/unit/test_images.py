import pytest

from aeropass.adapters.fakes.images import make_image, read_marker
from aeropass.domain.errors import FormatoNoAdmitido, ImagenDemasiadoGrande
from aeropass.domain.images import MAX_IMAGE_BYTES, validate_image


def test_accepts_jpeg_png_webp():
    assert validate_image(make_image("ok"), "image/jpeg").content_type == "image/jpeg"
    assert validate_image(make_image("ok", "PNG"), "image/png").content_type == "image/png"
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 10
    assert validate_image(webp, "image/webp").content_type == "image/webp"


def test_accepts_image_jpg_alias():
    assert validate_image(make_image("ok"), "image/jpg").content_type == "image/jpeg"


def test_rejects_declared_type_not_matching_bytes():
    with pytest.raises(FormatoNoAdmitido):
        validate_image(make_image("ok", "PNG"), "image/jpeg")


def test_rejects_unsupported_format():
    with pytest.raises(FormatoNoAdmitido):
        validate_image(b"GIF89a....", "image/gif")


def test_rejects_over_4mb():
    data = make_image("ok") + b"\x00" * MAX_IMAGE_BYTES
    with pytest.raises(ImagenDemasiadoGrande):
        validate_image(data, "image/jpeg")


def test_repr_never_contains_bytes():
    image = validate_image(make_image("secret"), "image/jpeg")
    assert "secret" not in repr(image)


@pytest.mark.parametrize("marker", ["ok", "spoof", "other", "timeout"])
def test_marker_roundtrip(marker):
    assert read_marker(make_image(marker)) == marker
    assert read_marker(make_image(marker, "PNG")) == marker

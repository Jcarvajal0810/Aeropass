"""US2 — responses of /v1/biometrics/verifications match contracts/openapi.yaml."""

import pytest

from aeropass.adapters.fakes.images import make_image
from aeropass.domain.images import MAX_IMAGE_BYTES
from tests.contract.openapi_helper import assert_matches
from tests.integration.helpers import register, verify

pytestmark = pytest.mark.usefixtures("db")

PATH = "/v1/biometrics/verifications"


@pytest.mark.parametrize("marker", ["ok", "spoof", "timeout"])
async def test_200_each_result(client, marker):
    await register(client, "c1")
    assert_matches(await verify(client, "c1", marker), PATH, "post", 200)


async def test_404(client):
    assert_matches(await verify(client, "nobody"), PATH, "post", 404)


async def test_409(client):
    await register(client, "c1")
    await verify(client, "c1", "ok")
    assert_matches(await verify(client, "c1", "ok"), PATH, "post", 409)


async def test_413_415(client):
    await register(client, "c1")
    big = make_image("ok") + b"\x00" * MAX_IMAGE_BYTES
    assert_matches(await verify(client, "c1", content=big), PATH, "post", 413)
    assert_matches(
        await verify(client, "c1", content=b"GIF89a", content_type="image/gif"),
        PATH,
        "post",
        415,
    )


async def test_503(client, container):
    await register(client, "c1")
    container.media_storage.fail_next_put = True
    assert_matches(await verify(client, "c1"), PATH, "post", 503)

"""US1 — responses of /v1/identity match contracts/openapi.yaml."""

import pytest

from aeropass.adapters.fakes.images import make_image
from aeropass.domain.images import MAX_IMAGE_BYTES
from tests.contract.openapi_helper import assert_matches
from tests.integration.helpers import register

pytestmark = pytest.mark.usefixtures("db")

PATH = "/v1/identity"


async def test_post_201_and_200(client):
    assert_matches(await register(client, "c1"), PATH, "post", 201)
    assert_matches(await register(client, "c1"), PATH, "post", 200)


async def test_post_409_both_codes(client):
    await register(client, "c1")
    assert_matches(await register(client, "c2"), PATH, "post", 409)
    assert_matches(await register(client, "c1", numero_documento="777777"), PATH, "post", 409)


async def test_post_413(client):
    big = make_image("ok") + b"\x00" * MAX_IMAGE_BYTES
    assert_matches(await register(client, "c1", foto=big), PATH, "post", 413)


async def test_post_415(client):
    assert_matches(
        await register(client, "c1", foto=b"GIF89a", foto_type="image/gif"), PATH, "post", 415
    )


async def test_post_422(client):
    assert_matches(await register(client, "c1", fecha_vencimiento="2000-01-01"), PATH, "post", 422)


async def test_post_503(client, container):
    container.media_storage.fail_next_put = True
    assert_matches(await register(client, "c1"), PATH, "post", 503)


async def test_get_me_200_404_401(client):
    headers = {"Authorization": "Bearer test:c1"}
    assert_matches(await client.get(f"{PATH}/me", headers=headers), f"{PATH}/me", "get", 404)
    await register(client, "c1")
    assert_matches(await client.get(f"{PATH}/me", headers=headers), f"{PATH}/me", "get", 200)
    assert_matches(await client.get(f"{PATH}/me"), f"{PATH}/me", "get", 401)

"""US4 — responses of /v1/passes and /.well-known/jwks.json match contracts/openapi.yaml."""

import pytest

from tests.contract.openapi_helper import assert_matches
from tests.integration.helpers import issue_pass, register, verified_passenger

pytestmark = pytest.mark.usefixtures("db")


async def test_post_201_and_get_200(client):
    await verified_passenger(client, "c1")
    created = await issue_pass(client, "c1")
    assert_matches(created, "/v1/passes", "post", 201)
    detail = await client.get(
        f"/v1/passes/{created.json()['credencial_id']}",
        headers={"Authorization": "Bearer test:c1"},
    )
    assert_matches(detail, "/v1/passes/{credencialId}", "get", 200)


async def test_post_403_422_429(client):
    await register(client, "c1")
    assert_matches(await issue_pass(client, "c1"), "/v1/passes", "post", 403)
    await verified_passenger(client, "c2")
    assert_matches(await issue_pass(client, "c2", "???"), "/v1/passes", "post", 422)
    for _ in range(30):
        await issue_pass(client, "c2")
    assert_matches(await issue_pass(client, "c2"), "/v1/passes", "post", 429)


async def test_get_404(client):
    await verified_passenger(client, "c1")
    response = await client.get(
        "/v1/passes/01925f3f-0000-7000-8000-000000000004",
        headers={"Authorization": "Bearer test:c1"},
    )
    assert_matches(response, "/v1/passes/{credencialId}", "get", 404)


async def test_jwks(client):
    response = await client.get("/.well-known/jwks.json")
    assert_matches(response, "/.well-known/jwks.json", "get", 200)
    assert "max-age" in response.headers["cache-control"]

"""Spec 003, SC-003/SC-004 — with the flag off (as in production) the header has no effect."""

from __future__ import annotations

import pytest

from aeropass.adapters.fakes.images import make_image
from aeropass.adapters.faults.wrappers import FaultInjectingBiometricProvider
from tests.integration.helpers import register

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_the_header_is_ignored_when_the_flag_is_off(client, container):
    assert not container.settings.fault_injection_enabled
    assert (await register(client, "u1")).status_code == 201

    response = await client.post(
        "/v1/biometrics/verifications",
        files={"selfie": ("selfie.jpg", make_image("ok"), "image/jpeg")},
        headers={"Authorization": "Bearer test:u1", "X-AeroPass-Fault": "blob_down,mxface_down"},
    )

    assert response.status_code == 200
    assert response.json()["resultado"] == "EXITOSO"
    assert "x-aeropass-fault-applied" not in response.headers


def test_no_wrapper_exists_when_the_flag_is_off(container):
    assert not isinstance(container.biometric_provider.inner, FaultInjectingBiometricProvider)
    assert type(container.media_storage).__name__ == "InMemoryMediaStorage"

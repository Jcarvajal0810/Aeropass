"""No document number or image bytes may reach the logs (constitution; tasks.md conventions)."""

import logging

import pytest

from tests.integration.helpers import DOC_DEFAULTS, register, verify

pytestmark = pytest.mark.usefixtures("db")


async def test_registration_and_verification_do_not_log_pii(client, container, caplog):
    caplog.set_level(logging.DEBUG)
    container.biometric_provider.inner  # noqa: B018 - build the provider under capture
    await register(client, "pii-user")
    await verify(client, "pii-user", "spoof")
    await verify(client, "pii-user", "timeout")  # exercises the warning path
    container.event_publisher.inner.down = True
    await verify(client, "pii-user", "ok")  # exercises the outbox warning path

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert DOC_DEFAULTS["numero_documento"] not in text
    assert DOC_DEFAULTS["nombre_completo"] not in text
    assert "MOCK:" not in text  # image bytes carry this marker
    assert "\\xff\\xd8" not in text

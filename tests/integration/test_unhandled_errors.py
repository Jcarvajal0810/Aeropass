"""US1 — unhandled exceptions reach Sentry without sensitive data (spec 002, FR-001, FR-006).

The routes are added only to the app under test; no database is needed.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from aeropass.domain.errors import DatosInvalidos
from aeropass.main import create_app

DOCUMENT = "9876543210"
TOKEN = "Bearer eyJ.secret"


class Body(BaseModel):
    numero_documento: str


def _app(container) -> FastAPI:
    app = create_app(container)

    @app.post("/test/boom/{item_id}")
    async def boom(item_id: str, body: Body) -> dict:
        numero_documento = body.numero_documento  # a local that must never leave
        raise RuntimeError(f"failed for {numero_documento}")

    @app.post("/test/domain")
    async def domain() -> dict:
        raise DatosInvalidos(detalles={"campos": ["numero_documento"]})

    @app.post("/test/validated")
    async def validated(body: Body) -> dict:
        return {}

    @app.post("/test/logs")
    async def logs() -> dict:
        logging.getLogger("httpx").info("HTTP Request: GET https://x?token=abc")
        try:
            raise ConnectionError("broker down")
        except ConnectionError:
            logging.getLogger("aeropass.services.outbox_dispatcher").exception(
                "post-commit publish failed; left for the scheduled dispatcher"
            )
        return {}

    return app


async def _post(app: FastAPI, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


async def _boom(app: FastAPI) -> httpx.Response:
    return await _post(
        app,
        "/test/boom/123?token=secret",
        json={"numero_documento": DOCUMENT},
        headers={"Authorization": TOKEN},
    )


async def test_unhandled_exception_is_captured_with_route_method_and_stack(
    container, sentry_capture
):
    response = await _boom(_app(container))

    assert response.status_code == 500
    [event] = sentry_capture.events
    assert event["transaction"] == "/test/boom/{item_id}"
    assert event["request"]["method"] == "POST"
    [exc] = event["exception"]["values"]
    assert exc["type"] == "RuntimeError"
    assert exc["mechanism"]["handled"] is False
    frames = exc["stacktrace"]["frames"]
    assert any(f.get("function") == "boom" for f in frames)


async def test_the_captured_error_carries_no_sensitive_data(container, sentry_capture):
    await _boom(_app(container))

    [event] = sentry_capture.events
    request = event["request"]
    assert "data" not in request
    assert "query_string" not in request
    assert {h.lower() for h in request.get("headers", {})} <= {"content-type", "user-agent"}
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    assert not any("vars" in f for f in frames)
    assert event["exception"]["values"][0]["value"] == "[redactado]"
    dump = sentry_capture.dump()
    assert TOKEN not in dump
    assert f'"{DOCUMENT}"' not in dump  # only source-context lines of this test may show it


@pytest.mark.parametrize("with_sentry", [False, True])
async def test_the_response_is_the_same_with_and_without_sentry(container, request, with_sentry):
    if with_sentry:
        request.getfixturevalue("sentry_capture")

    response = await _boom(_app(container))

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


async def test_domain_and_validation_errors_are_not_reported(container, sentry_capture):
    app = _app(container)

    domain = await _post(app, "/test/domain")
    invalid = await _post(app, "/test/validated", json={"otro": 1})

    assert domain.status_code == 422
    assert invalid.status_code == 422
    assert sentry_capture.events == []


async def test_aeropass_logged_exceptions_are_errors_and_library_info_is_dropped(
    container, sentry_capture
):
    response = await _post(_app(container), "/test/logs")

    assert response.status_code == 200
    [event] = sentry_capture.events
    assert event["logger"] == "aeropass.services.outbox_dispatcher"
    assert event["exception"]["values"][0]["type"] == "ConnectionError"
    assert not any(log["attributes"].get("logger.name") == "httpx" for log in sentry_capture.logs)
    assert "token=abc" not in str(sentry_capture.logs)
    assert "token=abc" not in str(event.get("breadcrumbs"))

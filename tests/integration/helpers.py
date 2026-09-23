"""Request builders shared by integration and contract tests."""

from __future__ import annotations

import zlib
from typing import Any

import httpx

from aeropass.adapters.fakes.images import make_image

DOC_DEFAULTS: dict[str, str] = {
    "nombre_completo": "Ana María Pérez",
    "tipo_documento": "CC",
    "numero_documento": "1020345678",
    "fecha_vencimiento": "2030-01-01",
}


async def register(
    client: httpx.AsyncClient,
    user_id: str,
    *,
    foto: bytes | None = None,
    foto_type: str = "image/jpeg",
    include_foto: bool = True,
    **fields: Any,
) -> httpx.Response:
    data = {**DOC_DEFAULTS, **{k: str(v) for k, v in fields.items() if v is not None}}
    files = (
        {"foto_documento": ("documento.jpg", foto or make_image("documento"), foto_type)}
        if include_foto
        else None
    )
    return await client.post(
        "/v1/identity",
        data=data,
        files=files,
        headers={"Authorization": f"Bearer test:{user_id}"},
    )


async def verify(
    client: httpx.AsyncClient,
    user_id: str,
    marker: str | None = "ok",
    *,
    content: bytes | None = None,
    content_type: str = "image/jpeg",
) -> httpx.Response:
    selfie = content if content is not None else make_image(marker)
    return await client.post(
        "/v1/biometrics/verifications",
        files={"selfie": ("selfie.jpg", selfie, content_type)},
        headers={"Authorization": f"Bearer test:{user_id}"},
    )


async def issue_pass(
    client: httpx.AsyncClient, user_id: str, codigo_vuelo: str = "AV9380"
) -> httpx.Response:
    return await client.post(
        "/v1/passes",
        json={"codigo_vuelo": codigo_vuelo},
        headers={"Authorization": f"Bearer test:{user_id}"},
    )


def document_number_for(user_id: str) -> str:
    return f"9{zlib.crc32(user_id.encode()) % 10**8:08d}"


async def verified_passenger(client: httpx.AsyncClient, user_id: str) -> dict[str, Any]:
    """Register + successful selfie. Returns the verification response body."""
    registered = await register(client, user_id, numero_documento=document_number_for(user_id))
    assert registered.status_code == 201, registered.text
    response = await verify(client, user_id, "ok")
    assert response.status_code == 200, response.text
    assert response.json()["resultado"] == "EXITOSO"
    return response.json()

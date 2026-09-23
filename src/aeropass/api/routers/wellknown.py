"""Public signing keys for the QR token (extension point for the checkpoint, online or offline)."""

from typing import Any

from fastapi import APIRouter, Depends, Response

from aeropass.api.deps import get_credential_verifier
from aeropass.domain.credential.signing import CredentialVerifier

router = APIRouter(tags=["well-known"])


@router.get("/.well-known/jwks.json")
async def claves_publicas_qr(
    response: Response, verifier: CredentialVerifier = Depends(get_credential_verifier)
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=300"
    return verifier.jwks()

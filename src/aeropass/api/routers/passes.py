"""US4 — /v1/passes."""

import uuid

from fastapi import APIRouter, Depends, status

from aeropass.api.deps import (
    get_current_user,
    get_lifecycle_service,
    get_pass_issuance_service,
    get_registration_service,
)
from aeropass.api.schemas import DetallePaseResponse, EmitirPaseRequest, PaseResponse
from aeropass.domain.errors import CredencialNoEncontrada, PasajeroNoRegistrado
from aeropass.ports.auth import AuthenticatedUser
from aeropass.services.credential_lifecycle_service import CredentialLifecycleService
from aeropass.services.pass_issuance_service import PassIssuanceService
from aeropass.services.registration_service import RegistrationService

router = APIRouter(prefix="/v1/passes", tags=["passes"])


@router.post("", response_model=PaseResponse, status_code=status.HTTP_201_CREATED)
async def emitir_pase(
    body: EmitirPaseRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: PassIssuanceService = Depends(get_pass_issuance_service),
) -> PaseResponse:
    return PaseResponse.from_issued(await service.issue(user, body.codigo_vuelo))


@router.get("/{credencial_id}", response_model=DetallePaseResponse)
async def obtener_pase(
    credencial_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    registration: RegistrationService = Depends(get_registration_service),
    lifecycle: CredentialLifecycleService = Depends(get_lifecycle_service),
) -> DetallePaseResponse:
    try:
        pasajero = await registration.get_mine(user)
    except PasajeroNoRegistrado as exc:
        raise CredencialNoEncontrada() from exc
    credencial, historial = await lifecycle.detail_for_owner(credencial_id, pasajero.id)
    return DetallePaseResponse.from_domain(credencial, historial)

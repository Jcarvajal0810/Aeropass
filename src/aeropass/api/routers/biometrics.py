"""US2/US3 — /v1/biometrics/verifications."""

from fastapi import APIRouter, Depends, File, UploadFile

from aeropass.api.deps import get_current_user, get_facade, get_registration_service
from aeropass.api.schemas import ResultadoVerificacionResponse
from aeropass.api.uploads import read_upload, reject_oversized_body
from aeropass.domain.errors import DatosInvalidos
from aeropass.domain.images import ImageInput
from aeropass.ports.auth import AuthenticatedUser
from aeropass.services.identity_verification_facade import IdentityVerificationFacade
from aeropass.services.registration_service import RegistrationService

router = APIRouter(prefix="/v1/biometrics", tags=["biometrics"])


@router.post(
    "/verifications",
    response_model=ResultadoVerificacionResponse,
    dependencies=[Depends(reject_oversized_body)],
)
async def verificar_selfie(
    selfie: UploadFile | None = File(None),
    user: AuthenticatedUser = Depends(get_current_user),
    registration: RegistrationService = Depends(get_registration_service),
    facade: IdentityVerificationFacade = Depends(get_facade),
) -> ResultadoVerificacionResponse:
    pasajero = await registration.get_mine(user)
    data, content_type = await read_upload(selfie)
    if not data:
        raise DatosInvalidos(detalles={"campos": ["selfie"]})
    outcome = await facade.verify_and_create_identity(
        pasajero, pasajero.documento, ImageInput(data=data, content_type=content_type or "")
    )
    return ResultadoVerificacionResponse.from_outcome(outcome)

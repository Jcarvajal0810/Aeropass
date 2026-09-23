"""US1 — /v1/identity."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from aeropass.api.deps import get_current_user, get_registration_service
from aeropass.api.schemas import PasajeroResponse
from aeropass.api.uploads import read_upload, reject_oversized_body
from aeropass.ports.auth import AuthenticatedUser
from aeropass.services.registration_service import RegistrationService

router = APIRouter(prefix="/v1/identity", tags=["identity"])


@router.post(
    "",
    response_model=PasajeroResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_oversized_body)],
)
async def registrar_documento(
    response: Response,
    nombre_completo: str | None = Form(None),
    tipo_documento: str | None = Form(None),
    numero_documento: str | None = Form(None),
    fecha_vencimiento: date | None = Form(None),
    foto_documento: UploadFile | None = File(None),
    user: AuthenticatedUser = Depends(get_current_user),
    service: RegistrationService = Depends(get_registration_service),
) -> PasajeroResponse:
    foto, content_type = await read_upload(foto_documento)
    pasajero, created = await service.register(
        user,
        nombre_completo=nombre_completo,
        tipo_documento=tipo_documento,
        numero_documento=numero_documento,
        fecha_vencimiento=fecha_vencimiento,
        foto=foto,
        foto_content_type=content_type,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return PasajeroResponse.from_domain(pasajero)


@router.get("/me", response_model=PasajeroResponse)
async def obtener_mi_pasajero(
    user: AuthenticatedUser = Depends(get_current_user),
    service: RegistrationService = Depends(get_registration_service),
) -> PasajeroResponse:
    return PasajeroResponse.from_domain(await service.get_mine(user))

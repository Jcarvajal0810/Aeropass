"""Internal endpoints invoked by QStash schedules (never by passengers)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from aeropass.api.deps import (
    get_lifecycle_service,
    get_outbox_dispatcher,
    verify_qstash_signature,
)
from aeropass.services.credential_lifecycle_service import CredentialLifecycleService
from aeropass.services.outbox_dispatcher import OutboxDispatcher

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_qstash_signature)],
    include_in_schema=False,
)


class DispatchResponse(BaseModel):
    eventos_entregados: int
    eventos_pendientes: int
    credenciales_expiradas: int


@router.post("/outbox/dispatch", response_model=DispatchResponse)
async def despachar_outbox(
    dispatcher: OutboxDispatcher = Depends(get_outbox_dispatcher),
    lifecycle: CredentialLifecycleService = Depends(get_lifecycle_service),
) -> DispatchResponse:
    """Idempotent: safe to call concurrently or repeatedly (claims use SKIP LOCKED + lease)."""
    report = await dispatcher.dispatch_due()
    expiradas = await lifecycle.sweep_expired()
    return DispatchResponse(
        eventos_entregados=report.entregados,
        eventos_pendientes=report.pendientes,
        credenciales_expiradas=expiradas,
    )

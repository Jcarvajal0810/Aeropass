"""GET /health — polled every minute by the Sentry uptime monitor (spec 002, FR-013a).

Unauthenticated and without internal details: the body only says whether the service is up.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from aeropass.api.deps import get_health_service
from aeropass.services.health_service import HealthService

router = APIRouter(tags=["health"])

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/health", include_in_schema=False)
async def salud(service: HealthService = Depends(get_health_service)) -> JSONResponse:
    report = await service.check()
    if report.ok:
        return JSONResponse({"estado": "ok"}, headers=_NO_STORE)
    return JSONResponse({"estado": "no_disponible"}, status_code=503, headers=_NO_STORE)

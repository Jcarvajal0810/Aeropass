"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aeropass.api.deps import Container
from aeropass.api.schemas import ErrorResponse
from aeropass.domain.errors import DatosInvalidos, DomainError


def _error_response(exc: DomainError) -> JSONResponse:
    body = ErrorResponse(codigo=exc.codigo, mensaje=exc.mensaje, detalles=exc.detalles)
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
    return JSONResponse(
        status_code=exc.http_status,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(
        title="AeroPass Backend",
        version="1.0.0",
        description="Registro, verificación biométrica, identidad digital y QR dinámico",
    )
    app.state.container = container or Container.from_env()

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        campos = sorted({".".join(str(p) for p in err["loc"][1:]) for err in exc.errors()})
        return _error_response(DatosInvalidos(detalles={"campos": campos}))

    _include_routers(app)
    return app


def _include_routers(app: FastAPI) -> None:
    from aeropass.api.routers import biometrics, identity, internal, passes, wellknown

    app.include_router(identity.router)
    app.include_router(biometrics.router)
    app.include_router(passes.router)
    app.include_router(wellknown.router)
    app.include_router(internal.router)


app = create_app()

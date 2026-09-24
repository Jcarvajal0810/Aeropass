"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aeropass.adapters.observability.sentry_setup import configure_observability
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
    container = container or Container.from_env()
    # Before FastAPI(): the Sentry integration must be active when the app is built (spec 002).
    configure_observability(container.settings)
    app = FastAPI(
        title="AeroPass Backend",
        version="1.0.0",
        description="Registration, biometric verification, digital identity and dynamic QR",
    )
    app.state.container = container

    # Open to any origin until the frontend has a fixed domain. Auth travels in the
    # Authorization header (not cookies), so credentials stay disabled.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],
    )

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

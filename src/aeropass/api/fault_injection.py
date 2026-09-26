"""``X-AeroPass-Fault`` middleware (spec 003, FR-003/FR-004).

Registered by ``create_app`` only with ``FAULT_INJECTION_ENABLED`` (which the settings refuse in
production). It turns the header into the request's fault plan, and answers with
``X-AeroPass-Fault-Applied`` listing the faults that actually fired, so whoever tests knows the
fault had an effect. Pure ASGI (no ``BaseHTTPMiddleware``) so the plan lives in the same context
as the endpoint.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from aeropass.adapters.faults.context import activate, deactivate, parse

logger = logging.getLogger("aeropass.faults")

FAULT_HEADER = b"x-aeropass-fault"
KEY_HEADER = b"x-aeropass-fault-key"
APPLIED_HEADER = b"x-aeropass-fault-applied"


class FaultInjectionMiddleware:
    def __init__(self, app: Any, secret: str = "") -> None:
        self.app = app
        self._secret = secret

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw = headers.get(FAULT_HEADER)
        if not raw:
            await self.app(scope, receive, send)
            return
        if self._secret and not hmac.compare_digest(
            headers.get(KEY_HEADER, b""), self._secret.encode()
        ):
            # Never log the key, only that it did not match.
            logger.warning("fault injection refused: missing or wrong key")
            await self.app(scope, receive, send)
            return

        plan, unknown = parse(raw.decode("latin-1"))
        for name in unknown:
            logger.warning("fault_injection_unknown: %s", name)
        if not plan.faults:
            await self.app(scope, receive, send)
            return

        async def send_with_applied(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and plan.applied:
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (APPLIED_HEADER, ",".join(sorted(plan.applied)).encode()),
                    ],
                }
            await send(message)

        token = activate(plan)
        try:
            await self.app(scope, receive, send_with_applied)
        finally:
            deactivate(token)

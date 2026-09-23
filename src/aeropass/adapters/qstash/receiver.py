"""Verifies that an internal request really comes from QStash (``Upstash-Signature``)."""

from qstash import Receiver
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from aeropass.domain.errors import NoAutenticado


class QStashSignatureVerifier:
    def __init__(self, current_key: str, next_key: str, public_base_url: str | None) -> None:
        self._receiver = Receiver(current_signing_key=current_key, next_signing_key=next_key)
        self._base = public_base_url.rstrip("/") if public_base_url else None

    async def verify(self, request: Request) -> None:
        signature = request.headers.get("upstash-signature")
        if not signature:
            raise NoAutenticado("Missing QStash signature")
        body = (await request.body()).decode("utf-8")
        # Behind Vercel's proxy the scheme/host seen by the app may differ from the public URL
        # QStash signed, so the URL claim is checked only when the public base URL is known.
        url = f"{self._base}{request.url.path}" if self._base else None
        try:
            await run_in_threadpool(
                self._receiver.verify, signature=signature, body=body, url=url, clock_tolerance=5
            )
        except Exception as exc:
            raise NoAutenticado("Invalid QStash signature") from exc

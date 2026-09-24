"""End-to-end smoke test of the full passenger flow against a real deployment (no frontend).

Usage::

    CLERK_SECRET_KEY=sk_... uv run python -m aeropass.tools.e2e_flow \
        [--base-url https://aeropass-lac.vercel.app] [--user-id user_...] [--delete-user] \
        [--only-register] [--nombre ...] [--tipo-documento CC|CE|PASAPORTE] \
        [--numero-documento ...] [--fecha-vencimiento YYYY-MM-DD]

Steps: a Clerk test user (new, unless ``--user-id``) gets a session through the Clerk Backend
API, then the script calls, in order, ``POST /v1/identity`` -> ``POST /v1/biometrics/verifications``
-> ``POST /v1/passes`` -> ``GET /v1/passes/{credencial_id}`` and prints status + body of each.
Requires ``BIOMETRIC_PROVIDER=mock`` on the deployment: the selfie carries ``MOCK:ok``.

``--only-register`` stops after ``POST /v1/identity`` and checks ``GET /v1/identity/me`` instead,
leaving the passenger in ``PENDIENTE_VERIFICACION`` (seeds an account for testing the selfie from
the app). The document flags override the default test document data.

Session strategy: ``POST /v1/sessions`` (testing-only, development instances). If Clerk refuses it
(production instance), falls back to a sign-in token redeemed on the Frontend API, whose host is
taken from ``CLERK_PUBLISHABLE_KEY`` or ``--frontend-api``. Session JWTs live ~60 s, so a fresh one
is minted before every call. Exit code 0 only when every step returned the expected result.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import time
from datetime import date, timedelta
from typing import Any

import httpx

from aeropass.adapters.fakes.images import make_image

CLERK_API = "https://api.clerk.com/v1"
DEFAULT_BASE_URL = "https://aeropass-lac.vercel.app"
FLIGHT_CODE = "AV9123"


class Clerk:
    def __init__(self, secret_key: str, frontend_api: str | None) -> None:
        self._http = httpx.Client(
            base_url=CLERK_API, headers={"Authorization": f"Bearer {secret_key}"}, timeout=20
        )
        self._frontend_api = frontend_api

    def create_user(self) -> str:
        suffix = f"{int(time.time())}{secrets.token_hex(2)}"
        payload: dict[str, Any] = {
            # "+clerk_test" addresses never send real email (Clerk test mode).
            "email_address": [f"aeropass-e2e-{suffix}+clerk_test@example.com"],
            "first_name": "E2E",
            "last_name": "Aeropass",
            "skip_password_requirement": True,
        }
        response = self._http.post("/users", json=payload)
        # Instances can require extra identifiers; add exactly the ones Clerk reports missing.
        missing = _missing_params(response)
        if missing:
            optional = {
                "username": f"aeropass_e2e_{suffix}",
                # +1 201 555 01XX are Clerk test numbers: no SMS is sent.
                "phone_number": [f"+1201555{secrets.randbelow(100) + 100:04d}"],
            }
            payload.update({name: optional[name] for name in missing if name in optional})
            response = self._http.post("/users", json=payload)
        _raise_for(response, "creating Clerk user")
        return str(response.json()["id"])

    def delete_user(self, user_id: str) -> None:
        _raise_for(self._http.delete(f"/users/{user_id}"), "deleting Clerk user")

    def create_session(self, user_id: str) -> str:
        response = self._http.post("/sessions", json={"user_id": user_id})
        if response.is_success:
            return str(response.json()["id"])
        print(f"  POST /v1/sessions refused ({response.status_code}); trying a sign-in token")
        return self._session_via_sign_in_token(user_id)

    def _session_via_sign_in_token(self, user_id: str) -> str:
        if not self._frontend_api:
            raise SystemExit(
                "Clerk refused POST /v1/sessions (production instance?). Set "
                "CLERK_PUBLISHABLE_KEY or pass --frontend-api so the sign-in token can be redeemed."
            )
        response = self._http.post(
            "/sign_in_tokens", json={"user_id": user_id, "expires_in_seconds": 300}
        )
        _raise_for(response, "creating sign-in token")
        ticket = response.json()["token"]
        # _is_native=1: Clerk answers with an Authorization header instead of browser cookies.
        redeem = httpx.post(
            f"https://{self._frontend_api}/v1/client/sign_ins?_is_native=1",
            data={"strategy": "ticket", "ticket": ticket},
            timeout=20,
        )
        _raise_for(redeem, "redeeming sign-in token on the Frontend API")
        session_id = redeem.json().get("response", {}).get("created_session_id")
        if not session_id:
            raise SystemExit(f"Sign-in did not create a session: {redeem.text[:500]}")
        return str(session_id)

    def session_jwt(self, session_id: str) -> str:
        response = self._http.post(f"/sessions/{session_id}/tokens", json={})
        _raise_for(response, "minting session token")
        return str(response.json()["jwt"])


def _raise_for(response: httpx.Response, action: str) -> None:
    if not response.is_success:
        raise SystemExit(f"Clerk error {action}: {response.status_code} {response.text[:500]}")


def _missing_params(response: httpx.Response) -> list[str]:
    if response.status_code != 422:
        return []
    try:
        errors = response.json().get("errors", [])
    except ValueError:
        return []
    return [
        str(name)
        for error in errors
        if error.get("code") == "form_data_missing"
        for name in error.get("meta", {}).get("param_names", [])
    ]


def frontend_api_from_publishable_key(key: str | None) -> str | None:
    """``pk_(test|live)_<base64("<fapi-host>$")>`` -> ``<fapi-host>``."""
    if not key or key.count("_") < 2:
        return None
    encoded = key.split("_", 2)[2]
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded.rstrip("$") or None


def show(step: str, response: httpx.Response) -> Any:
    print(
        f"\n=== {step}\n{response.request.method} {response.request.url.path} -> "
        f"{response.status_code} ({response.elapsed.total_seconds():.2f}s)"
    )
    try:
        body = response.json()
        print(json.dumps(body, indent=2, ensure_ascii=False))
    except ValueError:
        body = None
        print(response.text[:2000] or "<empty body>")
    return body


def run(
    base_url: str,
    clerk: Clerk,
    user_id: str | None,
    delete_user: bool,
    documento: dict[str, str],
    only_register: bool,
) -> bool:
    created_user = user_id is None
    if user_id is None:
        user_id = clerk.create_user()
        print(f"Clerk test user created: {user_id}")
    else:
        print(f"Using existing Clerk user: {user_id}")

    try:
        session_id = clerk.create_session(user_id)
        print(f"Clerk session: {session_id}")
        ok = _flow(base_url, lambda: clerk.session_jwt(session_id), documento, only_register)
    finally:
        if created_user and delete_user:
            clerk.delete_user(user_id)
            print(f"\nClerk test user deleted: {user_id}")
    return ok


def _flow(base_url: str, token: Any, documento: dict[str, str], only_register: bool) -> bool:
    def auth() -> dict[str, str]:
        return {"Authorization": f"Bearer {token()}"}

    with httpx.Client(base_url=base_url, timeout=30) as api:
        # 1. Document registration (201 new, 200 identical resubmission for a reused user).
        r = api.post(
            "/v1/identity",
            headers=auth(),
            data=documento,
            files={"foto_documento": ("documento.jpg", make_image("documento"), "image/jpeg")},
        )
        show("1. POST /v1/identity", r)
        if r.status_code not in (200, 201):
            return _fail("identity registration")

        if only_register:
            r = api.get("/v1/identity/me", headers=auth())
            body = show("2. GET /v1/identity/me", r)
            if r.status_code != 200 or not body or body.get("estado") != "PENDIENTE_VERIFICACION":
                return _fail("registration check (expected 200 + estado=PENDIENTE_VERIFICACION)")
            return True

        # 2. Selfie verification: the mock provider reads MOCK:ok -> EXITOSO.
        r = api.post(
            "/v1/biometrics/verifications",
            headers=auth(),
            files={"selfie": ("selfie.jpg", make_image("ok"), "image/jpeg")},
        )
        body = show("2. POST /v1/biometrics/verifications", r)
        if r.status_code != 200 or not body or body.get("resultado") != "EXITOSO":
            return _fail("biometric verification (expected 200 + resultado=EXITOSO)")

        # 3. Pass issuance.
        r = api.post("/v1/passes", headers=auth(), json={"codigo_vuelo": FLIGHT_CODE})
        body = show("3. POST /v1/passes", r)
        if r.status_code != 201 or not body or "credencial_id" not in body:
            return _fail("pass issuance (expected 201)")
        credencial_id = body["credencial_id"]

        # 4. Pass detail.
        r = api.get(f"/v1/passes/{credencial_id}", headers=auth())
        body = show(f"4. GET /v1/passes/{credencial_id}", r)
        if r.status_code != 200 or not body or body.get("credencial_id") != credencial_id:
            return _fail("pass detail (expected 200)")
    return True


def _fail(step: str) -> bool:
    print(f"\n[FAIL] FAILED at: {step}")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.environ.get("E2E_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--user-id", help="reuse this Clerk user instead of creating one")
    parser.add_argument("--delete-user", action="store_true", help="delete the created user")
    parser.add_argument("--frontend-api", help="Clerk Frontend API host (fallback flow only)")
    parser.add_argument(
        "--only-register", action="store_true", help="stop after POST /v1/identity (+ GET /me)"
    )
    parser.add_argument("--nombre", default="Prueba E2E Aeropass")
    parser.add_argument("--tipo-documento", default="PASAPORTE")
    parser.add_argument("--numero-documento", help="default: random E2E<hex>")
    parser.add_argument(
        "--fecha-vencimiento", default=(date.today() + timedelta(days=5 * 365)).isoformat()
    )
    args = parser.parse_args(argv)
    documento = {
        "nombre_completo": args.nombre,
        "tipo_documento": args.tipo_documento,
        "numero_documento": args.numero_documento or "E2E" + secrets.token_hex(5).upper(),
        "fecha_vencimiento": args.fecha_vencimiento,
    }

    secret = os.environ.get("CLERK_SECRET_KEY")
    if not secret:
        print("CLERK_SECRET_KEY is not set", file=sys.stderr)
        return 2
    frontend_api = args.frontend_api or frontend_api_from_publishable_key(
        os.environ.get("CLERK_PUBLISHABLE_KEY")
    )

    print(f"Target: {args.base_url}")
    ok = run(
        args.base_url.rstrip("/"),
        Clerk(secret, frontend_api),
        args.user_id,
        args.delete_user,
        documento,
        args.only_register,
    )
    passed = "[OK] REGISTRATION PASSED" if args.only_register else "[OK] FULL FLOW PASSED"
    print(f"\n{passed}" if ok else "\n[FAIL] FLOW DID NOT COMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

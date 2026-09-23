"""Ed25519 (EdDSA) signing of the QR token — single source of the signature rule (DRY).

The checkpoint team verifies tokens with ``CredentialVerifier`` or directly with the public keys
published at ``/.well-known/jwks.json`` (also offline — Strategy "contingencia").
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jwt.algorithms import OKPAlgorithm

ALGORITHM = "EdDSA"
CLOCK_SKEW_SECONDS = 5


class InvalidCredentialToken(Exception):
    pass


class CredentialSigner:
    def __init__(self, private_key: Ed25519PrivateKey, kid: str) -> None:
        self._key = private_key
        self.kid = kid

    @classmethod
    def from_pem(cls, pem: str, kid: str) -> CredentialSigner:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("QR_SIGNING_PRIVATE_KEY must be an Ed25519 private key")
        return cls(key, kid)

    @classmethod
    def generate(cls, kid: str) -> CredentialSigner:
        return cls(Ed25519PrivateKey.generate(), kid)

    def private_pem(self) -> str:
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._key, algorithm=ALGORITHM, headers={"kid": self.kid})

    @staticmethod
    def signature_of(token: str) -> str:
        return token.rsplit(".", 1)[1]


class CredentialVerifier:
    def __init__(self, public_keys: dict[str, Ed25519PublicKey]) -> None:
        self._keys = public_keys

    @classmethod
    def from_signer(cls, signer: CredentialSigner) -> CredentialVerifier:
        return cls({signer.kid: signer.public_key})

    def verify(self, token: str, *, now: datetime) -> dict[str, Any]:
        """Checks signature, key id and expiry against ``now`` (the caller's clock)."""
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self._keys.get(kid) if kid else None
            if key is None:
                raise InvalidCredentialToken("unknown key id")
            # Time claims are checked below against the injected clock, not the host clock.
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
            )
        except jwt.PyJWTError as exc:
            raise InvalidCredentialToken(str(exc)) from exc
        instante = int(now.timestamp())
        if instante >= int(claims["exp"]):
            raise InvalidCredentialToken("expired")
        if int(claims["iat"]) > instante + CLOCK_SKEW_SECONDS:
            raise InvalidCredentialToken("issued in the future")
        return claims

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        keys = []
        for kid, key in self._keys.items():
            jwk = json.loads(OKPAlgorithm.to_jwk(key))
            keys.append({**jwk, "kid": kid, "alg": ALGORITHM, "use": "sig"})
        return {"keys": keys}

from datetime import UTC, datetime

import jwt
import pytest

from aeropass.domain.credential.signing import (
    CredentialSigner,
    CredentialVerifier,
    InvalidCredentialToken,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
CLAIMS = {
    "jti": "01925f3f-0000-7000-8000-000000000004",
    "sub": "01925f39-0000-7000-8000-000000000001",
    "flt": "AV9380",
    "perms": ["embarque"],
    "iat": int(NOW.timestamp()),
    "exp": int(NOW.timestamp()) + 45,
}


@pytest.fixture
def signer() -> CredentialSigner:
    return CredentialSigner.generate(kid="k1")


def test_token_verifies_with_jwks_public_key(signer):
    token = signer.sign(CLAIMS)
    jwks = CredentialVerifier.from_signer(signer).jwks()
    (key,) = jwks["keys"]
    assert key == {**key, "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig", "kid": "k1"}
    public = jwt.PyJWK(key).key
    claims = jwt.decode(
        token, public, algorithms=["EdDSA"], options={"verify_exp": False, "verify_iat": False}
    )
    assert claims == CLAIMS
    assert jwt.get_unverified_header(token)["kid"] == "k1"


def test_verifier_checks_expiry_against_given_clock(signer):
    verifier = CredentialVerifier.from_signer(signer)
    token = signer.sign(CLAIMS)
    assert verifier.verify(token, now=NOW)["flt"] == "AV9380"
    with pytest.raises(InvalidCredentialToken):
        verifier.verify(token, now=datetime.fromtimestamp(CLAIMS["exp"], UTC))


def test_tampered_token_rejected(signer):
    token = signer.sign(CLAIMS)
    header, payload, signature = token.split(".")
    other = CredentialSigner.generate(kid="k1").sign({**CLAIMS, "flt": "XX1"})
    forged = ".".join([header, other.split(".")[1], signature])
    with pytest.raises(InvalidCredentialToken):
        CredentialVerifier.from_signer(signer).verify(forged, now=NOW)


def test_unknown_kid_rejected(signer):
    token = CredentialSigner.generate(kid="other").sign(CLAIMS)
    with pytest.raises(InvalidCredentialToken):
        CredentialVerifier.from_signer(signer).verify(token, now=NOW)


def test_signature_is_compact_ed25519(signer):
    token = signer.sign(CLAIMS)
    assert signer.signature_of(token) == token.rsplit(".", 1)[1]
    assert len(signer.signature_of(token)) == 86  # 64 bytes, base64url without padding


def test_pem_roundtrip(signer):
    again = CredentialSigner.from_pem(signer.private_pem(), kid="k1")
    token = again.sign(CLAIMS)
    assert CredentialVerifier.from_signer(signer).verify(token, now=NOW)["sub"] == CLAIMS["sub"]

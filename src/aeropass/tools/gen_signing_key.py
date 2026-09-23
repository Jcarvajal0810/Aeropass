"""Generate an Ed25519 key pair for signing QR credentials.

Usage: ``uv run python -m aeropass.tools.gen_signing_key [kid]`` → paste the output into .env
(or Vercel environment variables). Keep the private key secret; the public part is served at
``/.well-known/jwks.json``.
"""

import sys
from datetime import UTC, datetime

from aeropass.domain.credential.signing import CredentialSigner


def main(argv: list[str]) -> int:
    kid = argv[1] if len(argv) > 1 else f"qr-{datetime.now(UTC):%Y%m%d}"
    signer = CredentialSigner.generate(kid=kid)
    pem = signer.private_pem().strip().replace("\n", "\\n")
    print(f'QR_SIGNING_PRIVATE_KEY="{pem}"')
    print(f"QR_SIGNING_KID={kid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

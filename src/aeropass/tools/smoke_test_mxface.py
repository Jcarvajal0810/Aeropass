"""MANUAL smoke test against the REAL MxFace API. Spends 2 transactions (verify + liveness) of the
free-tier quota (4/day per API), so it asks for confirmation. Never run by pytest or CI.

Usage: ``uv run python -m aeropass.tools.smoke_test_mxface <selfie.jpg> <document.jpg>`` with
``MXFACE_SUBSCRIPTION_KEY`` set (environment or .env).
"""

import asyncio
import sys
from pathlib import Path

import httpx

from aeropass.adapters.biometrics.mxface_adapter import MxFaceAdapter
from aeropass.config import get_settings
from aeropass.domain.images import ImageInput, validate_image
from aeropass.domain.verification import Thresholds, evaluate_outcome
from aeropass.ports.biometric_provider import ProviderUnavailable


def _load(path: str) -> ImageInput:
    data = Path(path).read_bytes()
    return validate_image(data, "image/png" if data.startswith(b"\x89PNG") else "image/jpeg")


async def _run(selfie: ImageInput, document: ImageInput) -> int:
    settings = get_settings()
    thresholds = Thresholds(
        liveness=settings.biometric_liveness_threshold,
        comparacion=settings.biometric_match_threshold,
    )
    async with httpx.AsyncClient(timeout=30) as client:
        adapter = MxFaceAdapter(client, settings.mxface_subscription_key, settings.mxface_base_url)
        try:
            result = await adapter.evaluate(selfie, document)
        except ProviderUnavailable as exc:
            print(f"Provider unavailable: {exc}")
            return 2
    resultado, motivo = evaluate_outcome(result, thresholds)
    print("\n--- Real MxFace result ---")
    print(f"Liveness score:   {result.score_liveness:.4f} (threshold {thresholds.liveness})")
    print(f"Match score:      {result.score_comparacion:.1f} (threshold {thresholds.comparacion})")
    print(f"Outcome:          {resultado}")
    print(f"Failure reason:   {motivo}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    if not get_settings().mxface_subscription_key:
        print("MXFACE_SUBSCRIPTION_KEY is not set")
        return 1
    selfie, document = _load(argv[1]), _load(argv[2])
    print("This run spends 2 transactions of the MxFace daily quota (4/day on the free tier).")
    if input("Continue? [y/N]: ").strip().lower() not in {"y", "s"}:
        print("Cancelled.")
        return 0
    return asyncio.run(_run(selfie, document))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

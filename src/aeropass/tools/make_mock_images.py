"""Write the mock images used in the quickstart walkthrough.

Usage: ``uv run python -m aeropass.tools.make_mock_images ./mock-images``
"""

import sys
from pathlib import Path

from aeropass.adapters.fakes.images import make_image

FILES = {
    "ok.jpg": "ok",
    "spoof.jpg": "spoof",
    "other.jpg": "other",
    "timeout.jpg": "timeout",
    "documento.jpg": "documento",
}


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "mock-images")
    target.mkdir(parents=True, exist_ok=True)
    for name, marker in FILES.items():
        (target / name).write_bytes(make_image(marker))
    print(f"{len(FILES)} imágenes en {target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

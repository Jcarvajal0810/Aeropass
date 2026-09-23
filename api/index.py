"""Vercel entrypoint: the Python runtime serves the ASGI ``app`` exported here."""

import sys
from pathlib import Path

# src layout: make the package importable even if the runtime does not install the project.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aeropass.main import app  # noqa: E402

__all__ = ["app"]

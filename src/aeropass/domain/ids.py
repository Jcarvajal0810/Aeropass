"""Time-ordered identifiers (UUIDv7) generated in the application."""

import uuid

from uuid_utils import compat


def new_id() -> uuid.UUID:
    return compat.uuid7()

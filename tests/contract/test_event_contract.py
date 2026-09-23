"""US3 — event payloads match contracts/events/*.json (FR-014)."""

import json
from datetime import UTC, datetime

import pytest

from aeropass.domain.events import CredencialEmitidaV1
from aeropass.domain.identity import IdentidadDigital
from aeropass.domain.ids import new_id
from tests.contract.openapi_helper import EVENTS_DIR, event_validator

NOW = datetime(2026, 9, 23, 15, 4, 5, tzinfo=UTC)


def test_credencial_emitida_payload_matches_schema():
    identidad = IdentidadDigital.crear(
        id=new_id(), pasajero_id=new_id(), intento_origen_id=new_id(), ahora=NOW
    )
    event = CredencialEmitidaV1.from_identity(identidad, id=new_id(), ahora=NOW)
    payload = event.to_payload()

    validator = event_validator("credencial.emitida.v1.json")
    errors = list(validator.iter_errors(payload))
    assert not errors, [e.message for e in errors]
    assert payload["datos"]["tipo_credencial"] == "IDENTIDAD_DIGITAL"
    # FR-014: no document data, no image references
    serialized = json.dumps(payload)
    for forbidden in ("numero_documento", "nombre", "blob", "foto", "selfie"):
        assert forbidden not in serialized


@pytest.mark.parametrize("filename", ["credencial.emitida.v1.json", "validacion.fallida.v1.json"])
def test_schema_examples_are_valid(filename):
    schema = json.loads((EVENTS_DIR / filename).read_text(encoding="utf-8"))
    validator = event_validator(filename)
    for example in schema["examples"]:
        errors = list(validator.iter_errors(example))
        assert not errors, [e.message for e in errors]

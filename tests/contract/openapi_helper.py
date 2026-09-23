"""Validate HTTP responses against specs/001-persistencia-registro-qr/contracts/openapi.yaml."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_DIR = Path(__file__).resolve().parents[2] / "specs" / "001-persistencia-registro-qr"
OPENAPI_PATH = SPEC_DIR / "contracts" / "openapi.yaml"
EVENTS_DIR = SPEC_DIR / "contracts" / "events"
_ROOT_URI = "urn:aeropass:openapi"


@cache
def _spec() -> dict[str, Any]:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


@cache
def _registry() -> Registry:
    resource = Resource.from_contents(_spec(), default_specification=DRAFT202012)
    return Registry().with_resource(_ROOT_URI, resource)


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        pointer = node["$ref"].removeprefix("#/").split("/")
        target: Any = _spec()
        for part in pointer:
            target = target[part]
        node = target
    return node


def response_schema(path: str, method: str, status: int) -> dict[str, Any] | None:
    operation = _spec()["paths"][path][method.lower()]
    response = _resolve(operation["responses"][str(status)])
    content = response.get("content", {}).get("application/json")
    if content is None:
        return None
    return _rewrite_refs(content["schema"])


def _rewrite_refs(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: (f"{_ROOT_URI}{v}" if k == "$ref" and isinstance(v, str) else _rewrite_refs(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(v) for v in node]
    return node


def assert_matches(response: httpx.Response, path: str, method: str, status: int) -> None:
    assert response.status_code == status, (response.status_code, response.text)
    operation = _spec()["paths"][path][method.lower()]
    assert str(status) in operation["responses"], f"{status} not documented for {method} {path}"
    schema = response_schema(path, method, status)
    if schema is None:
        return
    validator = Draft202012Validator(schema, registry=_registry())
    errors = sorted(validator.iter_errors(response.json()), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


def event_validator(filename: str) -> Draft202012Validator:
    import json

    schema = json.loads((EVENTS_DIR / filename).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

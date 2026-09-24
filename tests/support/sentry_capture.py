"""In-memory Sentry transport for tests (spec 002, task T006).

``configure_observability`` runs with its real options; only the network is replaced. Nothing
leaves the process and no real DSN is needed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from aeropass.config import Settings

TEST_DSN = "https://public@sentry.invalid/1"


def _plain(attributes: dict[str, Any]) -> dict[str, Any]:
    """``{"k": {"value": v, "type": t}}`` → ``{"k": v}``."""
    return {
        k: v["value"] if isinstance(v, dict) and "value" in v else v for k, v in attributes.items()
    }


class CapturingTransport(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def flush(self, timeout: float, callback: Any = None) -> None:
        return None


@dataclass
class SentryCapture:
    transport: CapturingTransport

    def flush(self) -> None:
        sentry_sdk.flush()

    def _items(self, item_type: str) -> list[dict[str, Any]]:
        self.flush()
        out: list[dict[str, Any]] = []
        for envelope in self.transport.envelopes:
            for item in envelope.items:
                if item.type != item_type:
                    continue
                payload = item.payload.json
                if payload is None and item.payload.bytes:
                    payload = json.loads(item.payload.bytes)
                if item_type in ("log", "trace_metric"):
                    out.extend(payload["items"])
                else:
                    out.append(payload)
        return out

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._items("event")

    @property
    def transactions(self) -> list[dict[str, Any]]:
        return self._items("transaction")

    @property
    def logs(self) -> list[dict[str, Any]]:
        return [
            {**log, "attributes": _plain(log.get("attributes", {}))} for log in self._items("log")
        ]

    @property
    def metrics(self) -> list[dict[str, Any]]:
        return [
            {**m, "attributes": _plain(m.get("attributes", {}))}
            for m in self._items("trace_metric")
        ]

    def audit_logs(self, event: str | None = None) -> list[dict[str, Any]]:
        return [
            log
            for log in self.logs
            if "aeropass.event" in log["attributes"]
            and (event is None or log["attributes"]["aeropass.event"] == event)
        ]

    def metrics_named(self, name: str) -> list[dict[str, Any]]:
        return [m for m in self.metrics if m["name"] == name]

    def dump(self) -> str:
        """Every captured envelope as text, to assert that a value never leaves the process."""
        self.flush()
        chunks = []
        for envelope in self.transport.envelopes:
            for item in envelope.items:
                chunks.append(item.payload.get_bytes().decode("utf-8", "replace"))
        return "\n".join(chunks)


def sentry_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "aeropass_adapters": "fake",
        "biometric_provider": "mock",
        "sentry_dsn": TEST_DSN,
        "sentry_environment": "test",
        "sentry_traces_sample_rate": 1.0,
        "vercel_git_commit_sha": "test-release",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def sentry_capture() -> Iterator[SentryCapture]:
    from aeropass.adapters.observability.sentry_setup import (
        configure_observability,
        reset_observability,
    )

    transport = CapturingTransport()
    reset_observability()
    configure_observability(sentry_settings(), transport=transport)
    try:
        yield SentryCapture(transport)
    finally:
        reset_observability()

"""Instrumentation hooks (spec 002, research §6–§7). Written before the extension of hooks.py."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from aeropass.observability import hooks


@pytest.fixture(autouse=True)
def _clean_hooks() -> Iterator[None]:
    hooks.clear_hooks()
    yield
    hooks.clear_hooks()


@pytest.fixture
def spans() -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    @contextlib.contextmanager
    def hook(name: str) -> Iterator[None]:
        calls.append(("enter", name))
        try:
            yield
        finally:
            calls.append(("exit", name))

    hooks.register_span_hook(hook)
    return calls


@pytest.fixture
def audits() -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    hooks.register_audit_hook(lambda event, data: records.append((event, data)))
    return records


# --- span() ---------------------------------------------------------------------------------
def test_span_opens_and_closes_the_registered_hooks(spans):
    with hooks.span("circuit_breaker.biometric"):
        assert spans == [("enter", "circuit_breaker.biometric")]

    assert spans[-1] == ("exit", "circuit_breaker.biometric")


def test_span_reraises_the_same_exception(spans):
    error = TimeoutError("slow")

    with pytest.raises(TimeoutError) as caught, hooks.span("x"):
        raise error

    assert caught.value is error
    assert spans[-1] == ("exit", "x")


def test_a_failing_span_hook_never_breaks_the_business_call():
    def broken(name: str) -> Any:
        raise RuntimeError("telemetry down")

    hooks.register_span_hook(broken)

    @hooks.traced("step")
    def step() -> int:
        return 7

    assert step() == 7


# --- audited(describe=...) ------------------------------------------------------------------
def test_describe_adds_attributes_on_success_and_skips_none(audits):
    @hooks.audited("identity.verification", describe=lambda r: {"resultado": r, "motivo": None})
    def verify() -> str:
        return "EXITOSO"

    assert verify() == "EXITOSO"
    assert audits == [("identity.verification", {"outcome": "ok", "resultado": "EXITOSO"})]


async def test_describe_works_on_coroutines(audits):
    @hooks.audited("identity.verification", describe=lambda r: {"estado": r})
    async def verify() -> str:
        return "VERIFICADO"

    assert await verify() == "VERIFICADO"
    assert audits == [("identity.verification", {"outcome": "ok", "estado": "VERIFICADO"})]


def test_on_error_describe_is_not_called_and_only_the_type_is_emitted(audits):
    called = []

    @hooks.audited("credential.issue", describe=lambda r: called.append(r) or {})
    def issue() -> None:
        raise ValueError("document 1234567890 is invalid")

    with pytest.raises(ValueError):
        issue()

    assert called == []
    assert audits == [("credential.issue", {"outcome": "error", "error": "ValueError"})]


async def test_without_describe_behaviour_is_unchanged(audits):
    @hooks.audited("credential.issue")
    def issue() -> int:
        return 1

    @hooks.audited("credential.issue")
    async def issue_async() -> int:
        return 2

    assert issue() == 1
    assert await issue_async() == 2
    assert audits == [
        ("credential.issue", {"outcome": "ok"}),
        ("credential.issue", {"outcome": "ok"}),
    ]


def test_a_failing_describe_still_emits_and_returns(audits):
    def boom(result: Any) -> dict[str, str]:
        raise KeyError("x")

    @hooks.audited("identity.verification", describe=boom)
    def verify() -> str:
        return "EXITOSO"

    assert verify() == "EXITOSO"
    assert audits == [("identity.verification", {"outcome": "ok"})]


def test_a_failing_audit_hook_never_breaks_the_business_call():
    def broken(event: str, data: dict[str, Any]) -> None:
        raise RuntimeError("telemetry down")

    hooks.register_audit_hook(broken)

    @hooks.audited("credential.issue")
    def issue() -> int:
        return 1

    assert issue() == 1
    hooks.emit_audit("resilience.circuit_opened", dependencia="biometric")  # does not raise

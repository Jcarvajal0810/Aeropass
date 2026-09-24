"""Instrumentation hooks (constitution 1.1.0, Principle VI).

Business code only talks to these extension points: ``@traced``/``span()`` open spans and
``@audited``/``emit_audit()`` emit audit records, on whatever hooks are registered (the Sentry
sinks in ``adapters/observability``). With no hooks registered they are no-ops, and a failing
hook never breaks the business call.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

SpanHook = Callable[[str], AbstractContextManager[Any]]
AuditHook = Callable[[str, dict[str, Any]], None]
# Turns the value returned by an audited call into extra audit attributes (None = absent).
Describe = Callable[[Any], Mapping[str, Any]]

_span_hooks: list[SpanHook] = []
_audit_hooks: list[AuditHook] = []


def register_span_hook(hook: SpanHook) -> None:
    _span_hooks.append(hook)


def register_audit_hook(hook: AuditHook) -> None:
    _audit_hooks.append(hook)


def clear_hooks() -> None:
    _span_hooks.clear()
    _audit_hooks.clear()


@contextlib.contextmanager
def span(name: str) -> Iterator[None]:
    """Run a block inside every registered span hook; for names only known at runtime."""
    with contextlib.ExitStack() as stack:
        for hook in list(_span_hooks):
            try:
                stack.enter_context(hook(name))
            except Exception:  # telemetry must never break the caller
                logger.debug("span hook failed for %s", name, exc_info=True)
        yield


_spans = span


def emit_audit(event: str, **data: Any) -> None:
    for hook in list(_audit_hooks):
        try:
            hook(event, data)
        except Exception:  # telemetry must never break the caller
            logger.debug("audit hook failed for %s", event, exc_info=True)


def _described(describe: Describe | None, result: Any) -> dict[str, Any]:
    if describe is None:
        return {}
    try:
        attributes = describe(result)
    except Exception:
        logger.debug("audit describe failed", exc_info=True)
        return {}
    return {k: v for k, v in attributes.items() if v is not None and k != "outcome"}


def traced(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a sync or async callable in the registered span hooks."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with span(name):
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with span(name):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


def audited(
    event: str, describe: Describe | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Emit an audit record with the outcome (``ok``/``error``) of the wrapped callable.

    ``describe`` adds attributes derived from the returned value, only on success and only after
    the call finished (e.g. after its transaction committed).
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    emit_audit(event, outcome="error", error=type(exc).__name__)
                    raise
                emit_audit(event, outcome="ok", **_described(describe, result))
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                emit_audit(event, outcome="error", error=type(exc).__name__)
                raise
            emit_audit(event, outcome="ok", **_described(describe, result))
            return result

        return wrapper

    return decorator

"""Instrumentation hooks (constitution, Principle VI).

This repo only exposes the extension points: ``@traced`` and ``@audited`` call whatever hooks
the observability team registers (OpenTelemetry spans, Sentry, audit sinks). With no hooks
registered they are no-ops.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

SpanHook = Callable[[str], AbstractContextManager[Any]]
AuditHook = Callable[[str, dict[str, Any]], None]

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
def _spans(name: str) -> Iterator[None]:
    with contextlib.ExitStack() as stack:
        for hook in list(_span_hooks):
            stack.enter_context(hook(name))
        yield


def emit_audit(event: str, **data: Any) -> None:
    for hook in list(_audit_hooks):
        hook(event, data)


def traced(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a sync or async callable in the registered span hooks."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with _spans(name):
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with _spans(name):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


def audited(event: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Emit an audit record with the outcome (``ok``/``error``) of the wrapped callable."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    emit_audit(event, outcome="error", error=type(exc).__name__)
                    raise
                emit_audit(event, outcome="ok")
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                emit_audit(event, outcome="error", error=type(exc).__name__)
                raise
            emit_audit(event, outcome="ok")
            return result

        return wrapper

    return decorator

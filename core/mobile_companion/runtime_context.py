"""Request-scoped live runtime for companion commands on registry projections."""

from contextlib import contextmanager
from contextvars import ContextVar

_RUNTIME = ContextVar("mobile_runtime", default=None)


@contextmanager
def runtime_scope(runtime):
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def live_runtime():
    return _RUNTIME.get()

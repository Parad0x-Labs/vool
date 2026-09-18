"""Swappable selection of the active ``SemanticResolver``.

No subsystem hardcodes a resolver or imports one directly for use — the shadow/authority seam asks
this registry, and a deployment or a test registers the resolver it wants. This is the near-zero
blast-radius boundary: the resolver behind the seam can be replaced, upgraded, or removed without
touching the seam, and with nothing registered the seam is simply a no-op (the same state the
authority ladder floors to when no backend is available).

Registration is process-global and explicit. It grants a resolver the ability to be CONSULTED; it
grants no authority to execute anything — that remains ``core.semantic.admission``'s decision on
every proposal, whichever resolver produced it.
"""
from __future__ import annotations

import threading

from core.semantic.types import GraphSemanticResolver, SemanticResolver

_LOCK = threading.Lock()
_ACTIVE: SemanticResolver | None = None


def register_resolver(resolver: SemanticResolver) -> None:
    """Install ``resolver`` as the active one. Must satisfy the ``SemanticResolver`` Protocol.

    Rejects anything without a ``propose`` — a registry that accepted a non-resolver would defer the
    failure to the seam, where it would read as "the resolver returned nothing" rather than "nothing
    valid was registered".
    """
    if not isinstance(resolver, (SemanticResolver, GraphSemanticResolver)):
        raise TypeError(
            "a registered resolver must satisfy GraphSemanticResolver (prepare/finish) or the legacy "
            "SemanticResolver protocol (propose)"
        )
    global _ACTIVE
    with _LOCK:
        _ACTIVE = resolver


def active_resolver() -> SemanticResolver | None:
    """The registered resolver, or ``None`` when none is installed."""
    with _LOCK:
        return _ACTIVE


def has_resolver() -> bool:
    """Whether a resolver is installed — feeds the ladder's ``backend_available`` floor."""
    with _LOCK:
        return _ACTIVE is not None


def has_graph_resolver() -> bool:
    """Whether the installed resolver speaks the primary two-phase graph contract."""
    with _LOCK:
        return isinstance(_ACTIVE, GraphSemanticResolver)


def clear_resolver() -> None:
    """Remove the active resolver. Restores the no-op state; primarily for tests and teardown."""
    global _ACTIVE
    with _LOCK:
        _ACTIVE = None


__all__ = ["active_resolver", "clear_resolver", "has_graph_resolver", "has_resolver", "register_resolver"]

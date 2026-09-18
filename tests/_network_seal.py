"""One owner of this process's socket seal. Refusal is a UNION over an explicit policy stack.

**Composition is the whole reason this module exists.** Fixtures at different scopes each want to
forbid part of the network, and they are set up and torn down in an order no single fixture
controls. Patching `socket.socket.connect` directly makes the LAST writer the only policy, and
whether that writer is stricter or weaker than the one it replaced is decided by fixture ordering.
Ordering is not a security property.

Measured, not assumed: today's arrangement happens to compose correctly, because
`block_live_local_ollama_under_pytest` captured `socket.socket.connect` *live* and delegated to
whatever was already installed. A probe over `connect`/`connect_ex`/`create_connection` ×
IPv4/IPv6/unix/loopback showed every target still refused with a strict module-scoped seal beneath
it. But that is an accident of one line: had it captured a pristine reference instead -- the
obvious, and equally natural, way to write the same fixture -- the strict seal would have been
silently discarded for every test, and nothing would have said so.

So the invariant is made structural here rather than left to that accident:

    **Active network prohibitions may only stay equal or become stricter during a test.**

A policy is pushed onto a stack, and every guard consults ALL of them: the connection is refused if
ANY active policy refuses it. Adding a policy can therefore only make the process stricter; removing
one removes exactly that policy's refusals and no other's. A weaker policy pushed after a stronger
one cannot re-open anything, because it is never asked to permit -- only asked whether it, too,
refuses.

A policy is a callable `(kind, address) -> None` that **raises to refuse** and returns to abstain.
Raising rather than returning a reason lets each policy keep its own exception type: the root
conftest's local-Ollama guard raises `AssertionError` naming the opt-in flag, and a hermetic suite
raises `OSError`, which is what a socket caller is written to handle.
"""
from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from typing import Any

#: `(kind, address) -> None`. Raise to refuse; return to abstain. `kind` is one of the three
#: surfaces below, so one policy can treat a blocking connect differently from an errno-returning
#: `connect_ex` if it ever needs to.
Policy = Callable[[str, Any], None]

KIND_CONNECT = "connect"
KIND_CONNECT_EX = "connect_ex"
KIND_CREATE_CONNECTION = "create_connection"

#: The real implementations, captured once at import, before any fixture has run.
_PRISTINE_CONNECT = socket.socket.connect
_PRISTINE_CONNECT_EX = socket.socket.connect_ex
_PRISTINE_CREATE_CONNECTION = socket.create_connection

_LOCK = threading.RLock()
_STACK: list[tuple[int, str, Policy]] = []
_NEXT_TOKEN = 0

#: What the guards delegate to once every policy has abstained. Normally the pristine trio; after
#: `reassert()` finds a foreign patch, whatever that patch installed -- so a test that legitimately
#: wrapped a socket keeps its wrapper, underneath the union rather than instead of it.
_DELEGATE_CONNECT = _PRISTINE_CONNECT
_DELEGATE_CONNECT_EX = _PRISTINE_CONNECT_EX
_DELEGATE_CREATE_CONNECTION = _PRISTINE_CREATE_CONNECTION

_installed = False


def _run(kind: str, address: Any) -> None:
    """Consult every active policy. The first that raises refuses; the rest never run."""
    for _token, _name, policy in tuple(_STACK):
        policy(kind, address)


def _guard_connect(self, address):
    _run(KIND_CONNECT, address)
    return _DELEGATE_CONNECT(self, address)


def _guard_connect_ex(self, address):
    # `connect_ex` returns an errno rather than raising, which is how a caller walked through an
    # earlier seal that only patched `connect`. A refusing policy still raises here: an errno is a
    # value a caller may ignore, and a prohibition a caller can ignore is not one.
    _run(KIND_CONNECT_EX, address)
    return _DELEGATE_CONNECT_EX(self, address)


def _guard_create_connection(address, *args, **kwargs):
    _run(KIND_CREATE_CONNECTION, address)
    return _DELEGATE_CREATE_CONNECTION(address, *args, **kwargs)


_GUARDS = (_guard_connect, _guard_connect_ex, _guard_create_connection)


def _install() -> None:
    global _installed
    socket.socket.connect = _guard_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guard_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _guard_create_connection  # type: ignore[assignment]
    _installed = True


def _uninstall() -> None:
    global _installed, _DELEGATE_CONNECT, _DELEGATE_CONNECT_EX, _DELEGATE_CREATE_CONNECTION
    socket.socket.connect = _PRISTINE_CONNECT  # type: ignore[method-assign]
    socket.socket.connect_ex = _PRISTINE_CONNECT_EX  # type: ignore[method-assign]
    socket.create_connection = _PRISTINE_CREATE_CONNECTION  # type: ignore[assignment]
    _DELEGATE_CONNECT = _PRISTINE_CONNECT
    _DELEGATE_CONNECT_EX = _PRISTINE_CONNECT_EX
    _DELEGATE_CREATE_CONNECTION = _PRISTINE_CREATE_CONNECTION
    _installed = False


def push(name: str, policy: Policy) -> int:
    """Add a policy. Returns the token that releases exactly this one and nothing else."""
    global _NEXT_TOKEN
    with _LOCK:
        _NEXT_TOKEN += 1
        token = _NEXT_TOKEN
        _STACK.append((token, str(name), policy))
        if not _installed:
            _install()
        return token


def release(token: int) -> None:
    """Remove one policy. Every other active policy keeps refusing exactly what it refused."""
    with _LOCK:
        for index, (candidate, _name, _policy) in enumerate(_STACK):
            if candidate == token:
                del _STACK[index]
                break
        if not _STACK and _installed:
            _uninstall()


def active() -> tuple[str, ...]:
    """The names of the policies currently refusing, oldest first."""
    with _LOCK:
        return tuple(name for _token, name, _policy in _STACK)


def is_intact() -> bool:
    """Whether the union is the thing actually installed on all three surfaces."""
    with _LOCK:
        if not _STACK:
            return True
        return (
            socket.socket.connect is _guard_connect
            and socket.socket.connect_ex is _guard_connect_ex
            and socket.create_connection is _guard_create_connection
        )


def reassert() -> tuple[str, ...]:
    """Put the union back on top of whatever is installed. Returns the surfaces that were displaced.

    Called from a `pytest_runtest_call` hook, which runs after EVERY fixture has been set up and
    before the test body -- the one point in a test's life where "what is installed now" is not a
    question about ordering. Anything a fixture put on a socket surface becomes this module's
    delegate rather than its replacement, so a test that legitimately wraps a socket keeps its
    wrapper and gains the union above it. Strictness can only go up.
    """
    global _DELEGATE_CONNECT, _DELEGATE_CONNECT_EX, _DELEGATE_CREATE_CONNECTION
    with _LOCK:
        if not _STACK:
            return ()
        displaced: list[str] = []
        if socket.socket.connect is not _guard_connect:
            _DELEGATE_CONNECT = socket.socket.connect
            socket.socket.connect = _guard_connect  # type: ignore[method-assign]
            displaced.append(KIND_CONNECT)
        if socket.socket.connect_ex is not _guard_connect_ex:
            _DELEGATE_CONNECT_EX = socket.socket.connect_ex
            socket.socket.connect_ex = _guard_connect_ex  # type: ignore[method-assign]
            displaced.append(KIND_CONNECT_EX)
        if socket.create_connection is not _guard_create_connection:
            _DELEGATE_CREATE_CONNECTION = socket.create_connection
            socket.create_connection = _guard_create_connection  # type: ignore[assignment]
            displaced.append(KIND_CREATE_CONNECTION)
        # `_installed` is already True: the stack is non-empty, so `push` installed the guards.
        return tuple(displaced)


def snapshot_stack() -> tuple:
    """The exact active policies, TOKENS INCLUDED, for a test that must lift the seal briefly.

    Releasing everything and pushing it back is not equivalent, and the difference is a real leak
    that escaped into a shard run: `push` mints a NEW token, so the fixture holding the OLD one
    releases nothing at teardown and its policy stays on the stack for the rest of the process. The
    symptom was a `semantic_phase0` refusal landing on a local fixture HTTP server in an unrelated
    test file three modules later.
    """
    with _LOCK:
        return tuple(_STACK)


def restore_stack(entries: tuple) -> None:
    """Put back exactly what `snapshot_stack` returned, tokens preserved so releases still match."""
    global _installed
    with _LOCK:
        _STACK[:] = list(entries)
        if _STACK and not _installed:
            _install()
        elif not _STACK and _installed:
            _uninstall()


def clear_for_probe() -> tuple:
    """Lift every policy and return the snapshot needed to put them back. Pair with `restore_stack`."""
    entries = snapshot_stack()
    with _LOCK:
        _STACK.clear()
        if _installed:
            _uninstall()
    return entries


def deny_everything(reason: str) -> Policy:
    """The strictest policy: no address, on any family, on any surface.

    Loopback included. "Hermetic" that admits whatever happens to be listening on this machine --
    a local Ollama, a running daemon, a unix-socket service -- makes the result depend on the
    machine, which is the opposite of the property being claimed.
    """

    def _policy(kind: str, address: Any) -> None:
        detail = "" if kind == KIND_CONNECT else f" ({kind})"
        raise OSError(f"{reason}{detail}: {address!r}")

    return _policy


__all__ = [
    "KIND_CONNECT",
    "KIND_CONNECT_EX",
    "KIND_CREATE_CONNECTION",
    "Policy",
    "active",
    "clear_for_probe",
    "deny_everything",
    "is_intact",
    "push",
    "reassert",
    "release",
    "restore_stack",
    "snapshot_stack",
]

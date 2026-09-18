"""A weaker fixture may never replace a stronger active network prohibition.

The invariant, stated once: **active network prohibitions may only stay equal or become stricter
during a test.** They may never become weaker because another fixture ran later.

That property used to rest on an accident. `block_live_local_ollama_under_pytest` in the root
conftest captured `socket.socket.connect` *live* and delegated to it, so a strict module-scoped seal
underneath survived — but capturing a pristine reference instead, the equally natural way to write
the same fixture, would have discarded that seal on every test with nothing to say so. Whether the
suite was hermetic depended on one line in an unrelated fixture.

`tests/_network_seal` makes it structural: refusal is a union over an explicit policy stack, so a
policy pushed later can only ADD refusals, and a `pytest_runtest_call` hook re-applies the union on
top of anything a fixture installed directly — after every fixture has run and before the test body,
the one moment where "what is installed now" is not a question about ordering.

Every case here executes the prohibition rather than inspecting the arrangement.
"""
from __future__ import annotations

import socket

import pytest

from tests import _network_seal as network_seal

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (  # noqa: F401
    SEAL_REASON,
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)

#: Every surface × family this package claims to seal. `connect_ex` is here because it returns an
#: errno rather than raising, which is exactly how a caller walked through an earlier seal.
_TARGETS = (
    ("ipv4 public", socket.AF_INET, ("8.8.8.8", 53)),
    ("ipv4 loopback ollama", socket.AF_INET, ("127.0.0.1", 11434)),
    ("ipv4 loopback daemon", socket.AF_INET, ("127.0.0.1", 49152)),
    ("ipv6 loopback", socket.AF_INET6, ("::1", 11434, 0, 0)),
)


def _refusal(fn, *args) -> str:
    """Call and return the refusal, or a sentinel naming the escape."""
    try:
        fn(*args)
    except BaseException as exc:
        return f"{type(exc).__name__}: {exc}"
    return "REACHED THE NETWORK"


def _every_surface_refuses() -> list[str]:
    """Drive all three surfaces across every family. Returns the escapes, which must be none."""
    escapes: list[str] = []
    for label, family, address in _TARGETS:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            for surface, call in (("connect", sock.connect), ("connect_ex", sock.connect_ex)):
                answer = _refusal(call, address)
                if "REACHED" in answer:
                    escapes.append(f"{surface} {label}: {answer}")
        finally:
            sock.close()
    if hasattr(socket, "AF_UNIX"):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            for surface, call in (("connect", sock.connect), ("connect_ex", sock.connect_ex)):
                answer = _refusal(call, "/tmp/vool-seal-probe.sock")
                if "REACHED" in answer:
                    escapes.append(f"{surface} unix: {answer}")
        finally:
            sock.close()
    for label, _family, address in _TARGETS[:2]:
        answer = _refusal(socket.create_connection, address)
        if "REACHED" in answer:
            escapes.append(f"create_connection {label}: {answer}")
    return escapes


def test_the_strict_seal_survives_the_ordinary_root_fixture() -> None:
    """The three-step case the invariant is written for.

    1. The strict seal is installed (module scope, already active here).
    2. The ordinary root fixture runs afterwards (function scope, autouse, already ran).
    3. Every strict prohibition is still active — measured, on every surface and family.
    """
    assert "semantic_phase0:deny-everything" in network_seal.active()
    assert "root:no-live-local-ollama" in network_seal.active(), (
        "the root fixture must really be active, or this test proves nothing about composition"
    )
    assert _every_surface_refuses() == []


def test_a_policy_pushed_later_can_only_add_refusals() -> None:
    """A weaker policy arriving after a stronger one cannot re-open anything.

    It is never asked to permit — only asked whether it, too, refuses. Its abstention is not a
    grant.
    """

    def _permissive(_kind: str, _address: object) -> None:
        return  # abstains on everything: the weakest policy expressible

    token = network_seal.push("test:permissive", _permissive)
    try:
        assert _every_surface_refuses() == [], "an abstaining policy must not weaken the union"
    finally:
        network_seal.release(token)
    assert _every_surface_refuses() == []


def test_releasing_one_policy_leaves_every_other_refusal_standing() -> None:
    stricter = network_seal.push("test:also-deny", network_seal.deny_everything("second policy"))
    assert _every_surface_refuses() == []
    network_seal.release(stricter)
    assert _every_surface_refuses() == [], (
        "releasing an added policy must not release the ones that were already there"
    )


def test_a_direct_socket_patch_is_absorbed_rather_than_obeyed() -> None:
    """The hostile case: something bypasses the registry and installs a permissive callable.

    The union is re-applied on top by the `pytest_runtest_call` hook, so the patch becomes the
    union's delegate rather than its replacement. Here the hook has already run, so `reassert()` is
    called directly to stand for it — and the property asserted is the one that matters: after the
    absorption, nothing reaches the network.
    """
    reached: list[object] = []

    def _wide_open(_self, address):
        reached.append(address)
        return None  # pretends the connection succeeded

    original = socket.socket.connect
    socket.socket.connect = _wide_open  # type: ignore[method-assign]
    try:
        assert network_seal.is_intact() is False, "the tamper must be detectable"
        displaced = network_seal.reassert()
        assert network_seal.KIND_CONNECT in displaced
        assert network_seal.is_intact() is True
        assert _every_surface_refuses() == []
        assert reached == [], "the permissive patch must never have been consulted"
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]
        network_seal.reassert()


def test_the_refusal_names_this_package_so_a_block_is_attributable() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError, match=SEAL_REASON):
            sock.connect(("8.8.8.8", 53))
        with pytest.raises(OSError, match="connect_ex"):
            sock.connect_ex(("8.8.8.8", 53))
    finally:
        sock.close()


def test_the_probe_can_detect_an_escape() -> None:
    """The control. Without it, `_every_surface_refuses() == []` could be vacuously true.

    Everything is released, the pristine socket is back, and a connection to a port nothing is
    listening on must reach the network layer -- an ordinary refused/timed-out connection, not one
    of this package's refusals. Restored in `finally` so a failure here cannot leave the suite open.
    """
    # Tokens are preserved. Releasing everything and pushing it back is NOT equivalent: `push` mints
    # a new token, so the fixture holding the old one releases nothing at teardown and its policy
    # stays on the stack for the rest of the process. That leak escaped into a shard run, where a
    # `semantic_phase0` refusal landed on a local fixture HTTP server in an unrelated test file.
    active = network_seal.clear_for_probe()
    try:
        assert network_seal.active() == ()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.25)
        try:
            answer = _refusal(sock.connect, ("127.0.0.1", 1))
        finally:
            sock.close()
        assert SEAL_REASON not in answer, (
            f"with every policy released the seal must be gone, but got {answer!r}"
        )
    finally:
        network_seal.restore_stack(active)
        assert "semantic_phase0:deny-everything" in network_seal.active()
        assert _every_surface_refuses() == []


def test_lifting_the_seal_for_a_probe_does_not_orphan_the_policies_that_come_back() -> None:
    """The leak this class of test caused, pinned so it cannot come back.

    A probe that lifts every policy and then PUSHES them back mints new tokens. The fixture holding
    the old token then releases nothing at teardown, and its policy stays on the stack for the rest
    of the process. That escaped into a shard run: a `semantic_phase0` refusal landed on a local
    fixture HTTP server in `tests/test_missing_textual_content_repair.py`, three modules later, in a
    file with nothing to do with this package. Restoring must preserve tokens.
    """
    token = network_seal.push("test:leak-probe", network_seal.deny_everything("probe policy"))
    try:
        entries = network_seal.clear_for_probe()
        assert network_seal.active() == (), "the probe must really lift everything"
        network_seal.restore_stack(entries)
        assert "test:leak-probe" in network_seal.active()

        # The token the caller is holding must still release the policy it pushed.
        network_seal.release(token)
        assert "test:leak-probe" not in network_seal.active(), (
            "restore re-minted the token, so the original holder can no longer release its policy "
            "-- that is the leak"
        )
    finally:
        network_seal.release(token)

    # And the package's own policy survived the whole exercise.
    assert "semantic_phase0:deny-everything" in network_seal.active()
    assert _every_surface_refuses() == []

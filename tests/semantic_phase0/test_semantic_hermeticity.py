"""Nothing in this package can reach a socket. Every connect surface, not just the obvious one.

A hostile review walked straight through the previous seal with `connect_ex`, which returns an errno
instead of raising — so a caller that used it reached loopback and a unix-domain socket while the
suite reported itself hermetic. "Hermetic" that admits whatever happens to be listening on this
machine makes the result depend on the machine, which is the opposite of the property.

Every case below is a surface an HTTP client, a daemon probe or a local model runner actually uses.
"""
from __future__ import annotations

import socket

import pytest

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)


def test_connect_ex_is_sealed_for_loopback_and_unix_sockets() -> None:
    """The exact hole the review found. `connect_ex` must refuse, not return an errno."""
    ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError, match="blocked"):
            ipv4.connect_ex(("127.0.0.1", 11434))  # local Ollama's port, the realistic target
    finally:
        ipv4.close()

    if hasattr(socket, "AF_UNIX"):
        unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError, match="blocked"):
                unix.connect_ex("/tmp/vool-does-not-exist.sock")
        finally:
            unix.close()


def test_connect_is_sealed_on_every_address_family() -> None:
    for family, address in (
        (socket.AF_INET, ("127.0.0.1", 11434)),
        (socket.AF_INET, ("8.8.8.8", 53)),
        (socket.AF_INET6, ("::1", 11434, 0, 0)),
    ):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError, match="blocked"):
                sock.connect(address)
        finally:
            sock.close()


def test_unix_domain_connect_is_sealed() -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("no unix-domain sockets on this platform")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError, match="blocked"):
            sock.connect("/tmp/vool-does-not-exist.sock")
    finally:
        sock.close()


def test_create_connection_is_sealed() -> None:
    with pytest.raises(OSError, match="blocked"):
        socket.create_connection(("127.0.0.1", 11434), timeout=0.1)


def test_a_live_local_daemon_cannot_be_consulted() -> None:
    """The property that actually matters, stated as itself.

    Hermetic is not "outbound internet is blocked" -- it is "the candidate cannot consult anything
    on this machine either". If a local Ollama or a VOOL daemon happens to be running while this
    suite executes, no turn may reach it.
    """
    for port in (11434, 8188, 49152):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError, match="blocked"):
                sock.connect_ex(("127.0.0.1", port))
        finally:
            sock.close()

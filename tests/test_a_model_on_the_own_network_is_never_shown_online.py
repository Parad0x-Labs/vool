"""The answer-route chip calls a model on the person's own network "own network", never "online".

Trust-features audit of 900c7bc19: a model at a Tailscale-style 100.64/10 address (CGNAT) and one at "mymac.local"
were shown as "online" ("N of text went online"), while VOOL's local-only mode reads the same addresses as local. The
chip now keeps that one locality rule. Addresses are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core.answer_route import WHERE_ONLINE, WHERE_OWN_NETWORK, WHERE_THIS_COMPUTER, _where_of_url


@pytest.mark.parametrize("seed", range(6))
def test_own_network_addresses(seed) -> None:
    rng = random.Random(f"own-net-{seed}")
    host = rng.choice((
        f"100.{rng.randint(64, 127)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        f"192.168.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        rng.choice(("mymac.local", "studio-box.local", "ollama-host.local")),
    ))
    assert _where_of_url(f"http://{host}:11434", None) == WHERE_OWN_NETWORK, host


@pytest.mark.parametrize("url,where", [
    ("http://127.0.0.1:11434", WHERE_THIS_COMPUTER),
    ("http://8.8.8.8:11434", WHERE_ONLINE),
    ("http://100.63.0.1:11434", WHERE_ONLINE),
])
def test_this_computer_and_the_internet_are_unchanged(url, where) -> None:
    assert _where_of_url(url, None) == where

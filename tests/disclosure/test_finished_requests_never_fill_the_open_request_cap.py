"""Finished requests never fill the cap on open requests; only requests that wait on the owner count.

CI on 02c37a13b (tests/test_long_chat_facts_in_passing_v08.py, and the same red on 3dfb2f34): a served chat of 38 short
turns, each with its answer, the turn's tool judge and side calls, was refused on "what's my locker number now?" with
"Too many open requests. Close a preview and try again." No preview was open. Every call is kept for its preview and
comparison until the cache's age limit, and the cap of open requests counted those finished calls too.

Now the oldest requests nobody waits on make room, and the cap still refuses when that many really wait on the owner.
The cap is lowered here so the test stays short; the number of calls is drawn at random above it.
"""
from __future__ import annotations

import random

import pytest

from core.disclosure import wire
from tests.disclosure.test_wire import _send, cloud

_RNG = random.Random(20261123)


@pytest.fixture()
def small_cap(monkeypatch):
    monkeypatch.setattr(wire, "_LIMIT", 6)
    yield 6
    with wire._LOCK:
        for ident in [i for i, p in wire._PREVIEWS.items() if p.chat_id in ("wire-chat", "owner-waits")]:
            wire._PREVIEWS.pop(ident, None)


def test_many_finished_requests_in_a_row_are_all_sent(cloud, small_cap):
    url, bodies = cloud
    calls = small_cap * 2 + _RNG.randint(1, 9)
    for number in range(calls):
        reply = _send(url, f"short turn {number}", turn=f"turn-{number}", sensitive=False)
        assert reply["choices"][0]["message"]["content"] == "received", number
    assert len(bodies) == calls


def test_the_cap_still_refuses_when_that_many_wait_on_the_owner(cloud, small_cap):
    url, _bodies = cloud
    with wire._LOCK:
        for number in range(small_cap):
            item = wire.Preview(wire._home(), "owner-waits", f"waiting-{number}", "recording-provider", url, b"{}")
            wire._PREVIEWS[item.preview_id] = item
    with pytest.raises(wire.DisclosureRefused):
        _send(url, "one more", turn="one-more", sensitive=False)

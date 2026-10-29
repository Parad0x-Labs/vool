"""On a real served daemon, a turn from another device (or a channel) is refused the owner's tools.

The model's choice is scripted; ingress, routing, the fence and the tools are production code. The
loopback search transport records every request it is sent. Served chat never routes a turn to
pay.x402 for any peer, so its refusal is proven where the intent executes
(tests/test_remote_turn_tool_fence.py).
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests._owner_network_rig import CapturingProvider, NetworkDaemon, SearchTransport, model_choice
from tests._reader_served_rig import canonical_session

REFUSED_ASKS = ("web.search", "token.research")
PEERS = [pytest.param({"remote": True}, id="lan-peer"), pytest.param({"channel": True}, id="channel-gateway")]


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    with SearchTransport() as search, CapturingProvider(reply_fn=model_choice) as model:
        served = NetworkDaemon(tmp_path_factory.mktemp("served-refusals") / "home", search=search, provider=model)
        try:
            served.start()
            certification = served.certify(timeout=60)
            assert certification.get("state") == "verified", certification
            yield served, search
        finally:
            served.stop()


def _turn(served, text, **peer):
    return served.turn(text, session=canonical_session("served-refusal-" + uuid.uuid4().hex), **peer)


@pytest.mark.parametrize("peer", PEERS)
@pytest.mark.parametrize("intent", REFUSED_ASKS)
def test_another_device_is_refused_the_owners_research(daemon, peer, intent):
    served, search = daemon
    search.reset()
    result = _turn(served, f"Please run this. fixture-intent={intent}", **peer)
    assert "owner_local_required" in json.dumps(result), (result, served.log_tail())
    assert search.searches == [] and search.pages == [], (search.searches, search.pages)


def test_the_owners_own_turn_still_researches(daemon):
    served, search = daemon
    search.reset()
    result = _turn(served, "Please run this. fixture-intent=web.search")
    assert "owner_local_required" not in json.dumps(result), result
    assert search.searches, (result, served.log_tail())

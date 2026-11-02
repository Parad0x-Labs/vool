"""Privacy review 2026-11-02 of the default-on clean loop: a phone turn never reaches the loop.

THE DEFECT (delivery/clean-loop-08-default-on-20261101 @ 94ee1728)
------------------------------------------------------------------
The route asked only ``request_is_owner_local``, which trusts the server-stamped ``_owner_local``.
The chat door stamps it from the TCP peer alone (``is_loopback_host(client_host) and surface !=
"channel"``). A paired phone's ``chats.send`` (core/web/api/mobile_companion_api.py) reaches the
same door with ``surface="mobile_companion"`` and the phone's ``client_host``; when that traffic
arrives through anything on this computer (the companion relay, a local reverse proxy or tunnel),
the peer is loopback and the phone's turn is stamped owner-local. With the loop default ON (and
``VOOL_CLEAN_LOOP_CLOUD`` default ON) such a turn was admitted: the loop read the owner's files
for the phone and could spend the owner's cloud key, which brain.py promises never happens for a
phone or remote turn. A ``null_protocol`` service call (a remote agent's ``null://`` request)
has the same shape. The surface names the device, so it now decides first.
"""
from __future__ import annotations

from core.clean_loop import route

ON = {route.FLAG_ENV: "1"}


def _brain():
    return object()


def test_a_phone_turn_stamped_owner_local_over_loopback_is_not_admitted():
    """Fails before the fix: each context below was admitted ("admitted"), so the phone (or the
    remote agent) got an answer built from the owner's files, possibly by the owner's cloud model."""
    for context in (
        # The exact context the chat door builds for a mobile companion send from a loopback peer.
        {"surface": "mobile_companion", "platform": "mobile_companion", "_owner_local": True},
        {"surface": "MOBILE_COMPANION ", "_owner_local": True},
        {"surface": "api", "platform": "mobile_companion", "_owner_local": True},
        {"surface": "null_protocol", "_owner_local": True},
        {"surface": "channel", "_owner_local": True},
    ):
        decision = route.decide(
            "did i save the invoice for the boiler?", source_context=context, resolve_brain=_brain, env=ON
        )
        assert (decision.admit, decision.reason) == (False, "not_owner_local"), context


def test_the_owners_own_desktop_turn_is_still_admitted():
    """The other edge: the owner's own turns keep the loop."""
    for context in (
        {"surface": "cli"},
        {"surface": "web", "platform": "api", "_owner_local": True},
        {"surface": "desktop", "_owner_local": True},
    ):
        decision = route.decide(
            "did i save the invoice for the boiler?", source_context=context, resolve_brain=_brain, env=ON
        )
        assert (decision.admit, decision.reason) == (True, "admitted"), context

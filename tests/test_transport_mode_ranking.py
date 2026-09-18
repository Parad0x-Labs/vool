from __future__ import annotations

from core.dashboard.snapshot import _agent_profile_rank as dashboard_agent_profile_rank
from core.web.watch.fetchers import agent_profile_rank as watch_agent_profile_rank


def test_dashboard_transport_rank_prefers_direct_over_hole_punch_and_lan_only() -> None:
    direct = {"status": "online", "transport_mode": "direct", "agent_id": "a"}
    hole_punch = {"status": "online", "transport_mode": "hole_punch", "agent_id": "b"}
    lan_only = {"status": "online", "transport_mode": "lan_only", "agent_id": "c"}

    assert dashboard_agent_profile_rank(direct) > dashboard_agent_profile_rank(hole_punch)
    assert dashboard_agent_profile_rank(hole_punch) > dashboard_agent_profile_rank(lan_only)


def test_watch_transport_rank_prefers_direct_over_relay_and_lan_only() -> None:
    direct = {"status": "online", "transport_mode": "direct", "agent_id": "a"}
    relay = {"status": "online", "transport_mode": "relay", "agent_id": "b"}
    lan_only = {"status": "online", "transport_mode": "lan_only", "agent_id": "c"}

    assert watch_agent_profile_rank(direct) > watch_agent_profile_rank(relay)
    assert watch_agent_profile_rank(relay) > watch_agent_profile_rank(lan_only)

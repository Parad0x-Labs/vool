"""A whole housekeeping turn closes every demand it completed, and only those.

Measured on the served daemon (pack 1 re-audit): "rename this chat to Owned Audit and pin it"
renamed AND pinned the chat -- the store read both back -- yet the published turn certified
demand_minted=2, demand_satisfied=1, demand_indeterminate=1, covered=false. The lane executed both
demands and attested neither; the closure sweep found prose evidence for only one.

Each case runs a real agent turn and then the ledger's own closure sweep over the set that turn
minted, so the verdict is the one finalization publishes.
"""
from __future__ import annotations

import pytest

from core import runtime_paths

OWNER = {"surface": "api", "platform": "api", "_owner_local": True}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _turn(text: str, session: str, context: dict | None = None) -> tuple[dict, dict]:
    from core.agent_runtime.agent import VoolAgent
    from core.conductor import obligation_ledger

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    result = agent.run_once(text, source_context=dict(context or OWNER), session_id_override=session)
    stash = result.get("_closure_verdict") or {}
    set_id, version = str(stash.get("set_id") or ""), str(stash.get("set_version") or "")
    assert set_id and version, stash
    rows = {row["text"]: row["state"] for row in obligation_ledger.sweep_demand_obligations(set_id, version)}
    return result, {"rows": rows, **obligation_ledger.demand_census(set_id, version)}


def _session_row(session: str) -> dict:
    from core.memory.entries import list_conversation_sessions

    return next(r for r in list_conversation_sessions(limit=1000) if r["session_id"] == session)


def test_rename_and_pin_in_one_turn_closes_both_demands() -> None:
    from core import chat_housekeeping as hk

    session = "openclaw:hkclosure0000000000compound"
    result, census = _turn("rename this chat to Owned Audit and pin it", session)
    assert _session_row(session)["title"] == "Owned Audit"
    assert session in hk.pinned_chat_ids()
    assert "Renamed this chat to **Owned Audit**" in str(result.get("response"))
    assert census["demand_minted"] == 2, census
    assert census["demand_satisfied"] == 2, census
    assert set(census["rows"].values()) == {"satisfied"}, census


def test_a_refused_action_beside_a_completed_one_is_not_attested() -> None:
    # The chat we are talking in is never archived: that demand is refused, not satisfied, while
    # the rename beside it ran and is.
    session = "openclaw:hkclosure0000000000refused"
    result, census = _turn("rename this chat to Kept Title and archive it", session)
    assert _session_row(session)["title"] == "Kept Title"
    assert not _session_row(session).get("archived")
    assert "can't archive the chat we're talking in" in str(result.get("response"))
    assert census["demand_minted"] == 2, census
    assert census["demand_satisfied"] == 1, census
    archive = next(state for text, state in census["rows"].items() if "archive" in text)
    assert archive != "satisfied", census


def test_only_completed_actions_are_attested() -> None:
    from core.agent_runtime import housekeeping_lane as lane

    fresh = "openclaw:hkclosure00000000000unit01"
    done = lane.execute_plan(lane.parse_housekeeping("rename this chat to Budget and pin it"), session_id=fresh)
    assert done.completed == [0, 1], done.lines
    # forbidden by the turn's own words: nothing ran, nothing is attested
    held = lane.execute_plan(lane.parse_housekeeping("pin this chat"), session_id=fresh, mutations_allowed=False)
    assert held.completed == [], held.lines
    # "pin it" with nothing to point at asks; asking is not completing
    asked = lane.execute_plan(lane.parse_housekeeping("pin it"), session_id="openclaw:hkclosure00000000000unit02")
    assert asked.completed == [], asked.lines

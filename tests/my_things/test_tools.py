from __future__ import annotations

import pytest

from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.my_things.store import ThingsStore
from core.runtime_execution_tools import execute_runtime_tool
from tests.my_things.test_store import store


def turn(text):
    from core.agent_runtime.request_authority import REQUEST_PROVENANCE_KEY, request_provenance_for_visible_user_text
    from core.runtime_continuity import create_runtime_checkpoint

    session = "openclaw:things-proof"
    checkpoint = create_runtime_checkpoint(session_id=session, request_text=text, source_context={REQUEST_PROVENANCE_KEY: request_provenance_for_visible_user_text(text, session_id=session)})
    return {"_owner_local": True, "runtime_checkpoint_id": checkpoint["checkpoint_id"], "runtime_session_id": session, "session_id": session}


def test_real_runtime_tools_only_return_matched_records_for_a_proven_owner_turn(store):
    target = store.save({"name": "Cedar radio", "model": "RX-9112", "notes": "private note never searched"})
    store.save({"name": "Private other appliance", "model": "XX-4772"})
    ctx = turn("Find my Cedar radio, model RX-9112. The dial is broken")
    outcome = execute_authorized_runtime_tool("things.search", {"query": "Cedar radio"}, source_context=ctx)
    assert outcome.ok, outcome
    matched = outcome.details["observation"]["result"]["things"]
    assert [r["id"] for r in matched] == [target["id"]] and "notes" not in matched[0]
    outcome = execute_runtime_tool("things.match", {"text": "RX-9112"}, source_context=ctx)
    assert outcome.ok and outcome.details["observation"]["result"]["state"] == "match"
    outcome = execute_runtime_tool("things.prepare", {"id": target["id"], "kind": "repair", "issue": "The dial is broken"}, source_context=ctx)
    assert outcome.ok and outcome.details["observation"]["result"]["sent"] is False
    assert execute_runtime_tool("things.search", {"query": "Private other appliance"}, source_context=ctx).ok is False
    assert execute_runtime_tool("things.search", {"query": "Cedar radio"}, source_context={"_owner_local": True}).ok is False
    assert execute_runtime_tool("things.search", {"query": "Cedar radio"}, source_context={**ctx, "_owner_local": False}).ok is False
    assert execute_runtime_tool("things.search", {"query": "Cedar radio"}, source_context={**ctx, "turn_author": "agent"}).ok is False


def test_private_records_are_not_offered_to_a_model_before_admission_is_integrated(store):
    from core.capability_graph import _CANONICAL_FAMILIES, model_visible_specs
    from core.runtime_tool_contracts import runtime_tool_contracts

    contracts = [c for c in runtime_tool_contracts() if c.intent.startswith("things.")]
    assert {c.intent for c in contracts} == {"things.search", "things.match", "things.prepare"}
    assert all(not c.model_visible for c in contracts)
    for family in _CANONICAL_FAMILIES:
        offered = model_visible_specs(user_text="Find my saved headphones in My things", family_hint=family)
        assert all(not spec["intent"].startswith("things.") for spec in offered)

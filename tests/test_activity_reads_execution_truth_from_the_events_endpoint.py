"""The Activity panel must be SERVED execution truth, not left to re-derive it.

`core/execution_truth.py` makes one execution produce one authoritative fact, and
`tests/test_execution_truth_is_single_sourced.py` proves that module. But the Activity panel does
not import that module -- it is JavaScript. It learns what a turn executed from the
`execution_truth` block on `GET /api/runtime/events`, and falls back to scanning raw event types
when the block is absent. That scan is the defect the module exists to remove: it recognises only
`tool_executed`/`tool_selected`, so it rendered "No tool ran -- answered directly" over 80 turns
whose retrieval was recorded under a different event type.

Measured on the convergence candidate: with the endpoint's per-turn summary replaced by a lie
(`ran_tool=False, tool_names=[]`, import still used so Ruff stays quiet), the whole authoritative
gate passed -- 19,483 items, lint clean, `VERIFICATION PASSED`. Every test of the module still held,
because the module was still right; nothing asserted the answer reaches the panel. So the one seam
between "the runtime knows what ran" and "the user is told what ran" was unguarded, and the panel
could silently return to guessing.

Driven through the real `dispatch_get` and the real `emit_runtime_event`, using the thin live-data
event shape -- no `action_record`, no receipt -- because that is exactly the shape the raw scan gets
wrong. A test that called `turn_execution_summary` directly would prove the module again and nothing
about the endpoint.
"""

from __future__ import annotations

import json

from core.execution_truth import SCHEMA
from core.runtime_task_events import emit_runtime_event
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get

SESSION = "openclaw:activity-endpoint-truth"


def _context(turn_id: str, session: str = SESSION) -> dict[str, object]:
    """The real shape a turn carries: the client turn id arrives as `cancel_turn_id`."""
    return {"session_id": session, "runtime_session_id": session, "cancel_turn_id": turn_id}


def _emit_thin_execution(turn_id: str, tool: str, *, session: str = SESSION) -> None:
    """The live-data lane's own event: no action_record, no receipt, no tool_call_id."""
    emit_runtime_event(
        _context(turn_id, session),
        event_type="tool_executed",
        message=f"Finished {tool}: available",
        details={"tool_name": tool, "summary": f"Finished {tool}: available", "client_turn_id": turn_id},
    )


def _events_payload(session: str = SESSION) -> dict:
    response = dispatch_get(
        path="/api/runtime/events",
        query={"session": [session], "after": ["0"], "limit": ["400"]},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
    )
    return json.loads(response.body.decode("utf-8"))


def test_the_events_endpoint_serves_the_authoritative_summary_for_an_executed_turn() -> None:
    """A real execution reaches the panel as an answer, not as rows for it to interpret."""
    turn = "turn-activity-endpoint-executed"
    _emit_thin_execution(turn, "live_data.weather_lookup")

    payload = _events_payload()
    truth = (payload.get("execution_truth") or {}).get(turn)

    assert truth, "the endpoint served no authoritative summary for a turn that really executed"
    assert truth.get("schema") == SCHEMA
    assert truth.get("ran_tool") is True, (
        "the endpoint told the panel no tool ran on a turn that executed one -- "
        "the panel would render 'No tool ran -- answered directly' over real work"
    )
    assert "live_data.weather_lookup" in (truth.get("tool_names") or [])
    assert int(truth.get("tool_count") or 0) >= 1


def test_a_turn_that_executed_nothing_is_served_as_having_executed_nothing() -> None:
    """The negative control: the block must be able to say 'no', or asserting 'yes' proves nothing."""
    executed_turn = "turn-activity-endpoint-mixed-executed"
    quiet_turn = "turn-activity-endpoint-mixed-quiet"
    _emit_thin_execution(executed_turn, "live_data.market_quote")
    emit_runtime_event(
        _context(quiet_turn),
        event_type="task_completed",
        message="answered directly",
        details={"client_turn_id": quiet_turn},
    )

    truths = _events_payload().get("execution_truth") or {}

    assert truths.get(executed_turn, {}).get("ran_tool") is True
    quiet = truths.get(quiet_turn)
    assert quiet is not None, "a turn present in the stream must still get an authoritative answer"
    assert quiet.get("ran_tool") is False
    assert (quiet.get("tool_names") or []) == []


def test_one_turns_execution_is_not_served_against_another_turn() -> None:
    """Turn-scoped, because evidence-shaped and wrong is worse than the empty list it replaced."""
    mine = "turn-activity-endpoint-scope-mine"
    theirs = "turn-activity-endpoint-scope-theirs"
    _emit_thin_execution(mine, "live_data.weather_lookup")
    emit_runtime_event(
        _context(theirs),
        event_type="task_completed",
        message="answered directly",
        details={"client_turn_id": theirs},
    )

    truths = _events_payload().get("execution_truth") or {}

    assert truths.get(mine, {}).get("ran_tool") is True
    assert truths.get(theirs, {}).get("ran_tool") is False
    assert truths[mine]["turn_key"] == mine
    assert truths[theirs]["turn_key"] == theirs


def test_another_sessions_execution_never_appears_in_this_sessions_summary() -> None:
    """Chat isolation at the endpoint, not only in the store the endpoint reads."""
    other_session = "openclaw:activity-endpoint-truth-other"
    foreign_turn = "turn-activity-endpoint-foreign"
    _emit_thin_execution(foreign_turn, "live_data.weather_lookup", session=other_session)

    truths = _events_payload().get("execution_truth") or {}

    assert foreign_turn not in truths, "another chat's turn was served into this chat's Activity"


def test_two_chats_sharing_one_client_turn_id_do_not_share_an_execution() -> None:
    """The endpoint must scope the lookup by session, not by turn key alone.

    `turn_id` arrives in the /api/chat body -- it is the CLIENT's name for the turn, and two chats
    can honestly submit the same one (two tabs both on their first turn, any scripted caller with a
    counter). Turn key alone is therefore not an identity; turn key WITHIN a session is. Dropping
    the session argument leaves every other assertion in this file green, because the rest use
    distinct turn ids, while a quiet chat inherits the other chat's tool run in its Activity.
    """
    shared_turn = "t1"
    quiet_session = "openclaw:activity-endpoint-collision-quiet"
    busy_session = "openclaw:activity-endpoint-collision-busy"

    _emit_thin_execution(shared_turn, "live_data.weather_lookup", session=busy_session)
    emit_runtime_event(
        _context(shared_turn, quiet_session),
        event_type="task_completed",
        message="answered directly",
        details={"client_turn_id": shared_turn},
    )

    busy = (_events_payload(busy_session).get("execution_truth") or {}).get(shared_turn) or {}
    quiet = (_events_payload(quiet_session).get("execution_truth") or {}).get(shared_turn) or {}

    assert busy.get("ran_tool") is True
    assert quiet.get("ran_tool") is False, (
        "a chat that executed nothing was served another chat's tool run because both turns "
        "carried the same client turn id"
    )
    assert (quiet.get("tool_names") or []) == []


def test_a_receipt_only_retrieval_turn_is_served_as_having_run() -> None:
    """The confirmed gap, at the served seam: governed retrieval with no tool event of any kind.

    The research lane emits a `web_retrieval_completed` receipt and never a `tool_executed`, so at
    base the served summary read `ran_tool=False` with nothing else -- and the panel rendered
    "No tool ran -- answered directly" over a turn that had really reached Brave. The endpoint must
    now serve the retrieval under its own category, with `ran_tool` still honestly false.
    """
    turn = "turn-activity-endpoint-retrieval-only"
    emit_runtime_event(
        _context(turn),
        event_type="web_retrieval_completed",
        message="Web retrieval: brave, keyed",
        details={
            "schema": "vool.web_retrieval_receipt.v1",
            "retrieval_id": "web-retrieval-endpoint-1",
            "kind": "web_search",
            "action": "search",
            "status": "available",
            "provider_id": "brave",
            "source_count": 2,
            "client_turn_id": turn,
        },
    )

    truth = (_events_payload().get("execution_truth") or {}).get(turn) or {}

    assert truth.get("ran_retrieval") is True, (
        "the endpoint told the panel nothing ran on a turn whose receipt proves governed retrieval"
    )
    assert truth.get("ran_tool") is False
    assert int(truth.get("retrieval_count") or 0) == 1
    witness = truth.get("witness") or {}
    assert witness.get("consistent") is True

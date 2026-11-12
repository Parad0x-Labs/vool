"""A served turn's boundary re-check judges the honesty seal under the turn key the seal used.

Measured on 247124ed and again on bb35ff83 (served /api/chat, buffered): the model ran
`python3 build.py` through `sandbox__run_command` and then answered empty, so the tool loop
answered with its own grounded fallback ("... 1 of 1 tool steps succeeded. Last result: Command
executed in `.`: ..."). The in-turn seal passed that true claim: the command's execution fact is
recorded under the turn id `prepare_runtime_checkpoint` mints inside `run_once`. The door's
verify-only re-check held only its own pre-run context, resolved the turn to the request id, found
no execution there, rewrote the reply, and the K-08 assert turned a correct turn into HTTP 500.

The seal now stashes the key it judged with on the admitted payload, and both served boundaries
re-check under it.
"""

from __future__ import annotations

from core.agent_runtime import agent
from core.agent_runtime.action_honesty_validator import (
    SEALED_TURN_KEY,
    completion_claim_kind,
    enforce_final_action_honesty,
    sealed_turn_context,
)
from core.execution_truth import record_execution, resolve_turn_key
from core.semantic.semantic_result_seam import reset_admission

PROMPT = "Run the build script in my project with python3 and tell me what happened"
FALLBACK = (
    "Incomplete — model synthesis failed (empty_synthesis). The grounded work collected so far "
    "is below; this is not a completed audit.\n\n1 of 1 tool steps succeeded. Last result: "
    "Command executed in `.`:"
)


def _honesty(result: dict, *, session: str, context: dict) -> dict:
    return enforce_final_action_honesty(
        dict(result),
        user_input=PROMPT,
        effective_input=PROMPT,
        session_id=session,
        source_context=context,
    )


def test_a_true_command_claim_survives_the_boundary_recheck() -> None:
    session = "sealed-turn-key-served-boundary"
    record_execution(
        session_id=session, turn_key="turn-run-once", kind="tool", name="sandbox.run_command", ok=True
    )
    # The fallback claims a finished action, so the guard needs this turn's execution fact for it.
    assert completion_claim_kind(FALLBACK) == "mutation"
    in_turn = {"surface": "api", "request_id": "req-door", "turn_id": "turn-run-once"}
    door = {"surface": "api", "request_id": "req-door"}
    reply = {"response": FALLBACK, "mode": "tool_failed", "confidence": 0.6}

    sealed = _honesty(reply, session=session, context=in_turn)
    assert sealed["response"] == FALLBACK
    assert "action_honesty_validator" not in sealed

    # The failure: the door's own context resolves another key, so the same true claim reads as
    # unbacked and is rewritten -- the divergence the K-08 assert refused with a 500.
    assert resolve_turn_key(door) == "req-door"
    blind = _honesty(reply, session=session, context=door)
    assert blind["response"] != FALLBACK
    assert blind["action_honesty_validator"]["reason"] == "no_executed_action_receipt"

    # The fix: the boundary judges under the key the seal stashed, so the verify is a no-op.
    stashed = {**reply, SEALED_TURN_KEY: "turn-run-once"}
    rechecked = _honesty(stashed, session=session, context=sealed_turn_context(stashed, door))
    assert rechecked["response"] == FALLBACK
    assert "action_honesty_validator" not in rechecked


def test_the_stashed_key_never_backs_a_claim_its_turn_did_not_run() -> None:
    session = "sealed-turn-key-no-run"
    record_execution(
        session_id=session, turn_key="turn-other", kind="tool", name="sandbox.run_command", ok=True
    )
    door = {"surface": "api", "request_id": "req-door"}
    stashed = {"response": FALLBACK, "mode": "tool_failed", SEALED_TURN_KEY: "turn-run-once"}
    rechecked = _honesty(stashed, session=session, context=sealed_turn_context(stashed, door))
    assert rechecked["response"] != FALLBACK
    assert rechecked["action_honesty_validator"]["reason"] == "no_executed_action_receipt"


def test_sealed_turn_context_adds_the_key_and_leaves_the_door_context_alone() -> None:
    door = {"surface": "api", "request_id": "req-door"}
    context = sealed_turn_context({SEALED_TURN_KEY: "turn-run-once"}, door)
    assert resolve_turn_key(context) == "turn-run-once"
    assert door == {"surface": "api", "request_id": "req-door"}
    # No stash (a lane the in-turn seal never ran on): the door's own resolution is unchanged.
    plain = sealed_turn_context({"response": "hi"}, door)
    assert plain == door and plain is not door
    assert resolve_turn_key(sealed_turn_context(None, None)) == ""


def test_the_seal_stashes_the_turn_key_it_judged_with() -> None:
    reset_admission()
    result = agent._seal_semantic_result(
        {"response": "Here is the answer.", "model_calls": 1, "route_reason": "model_lane_guard_control"},
        session_id="sealed-turn-key-stash",
        user_input="What is the answer?",
        source_context={"surface": "cli", "request_id": "req-door", "turn_id": "turn-run-once"},
    )
    assert result["response"] == "Here is the answer."
    assert result[SEALED_TURN_KEY] == "turn-run-once"

    # A client-sent turn id outranks the minted one, and the stash follows the same resolution.
    reset_admission()
    result = agent._seal_semantic_result(
        {"response": "Here is the answer.", "model_calls": 1, "route_reason": "model_lane_guard_control"},
        session_id="sealed-turn-key-stash",
        user_input="What is the answer?",
        source_context={
            "surface": "cli", "request_id": "req-door", "turn_id": "turn-run-once", "cancel_turn_id": "client-7",
        },
    )
    assert result[SEALED_TURN_KEY] == "client-7"

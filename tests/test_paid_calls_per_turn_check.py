"""ops/paid_calls_per_turn.py: the per-turn paid-call count the Mac check reads (known v0.8 cost:
a declined clean-loop turn on a paid pick pays again on the tool lane)."""
from __future__ import annotations

from ops.paid_calls_per_turn import count_paid_calls


def _call(turn: str, role: str, cost: str = "paid_cloud") -> dict:
    return {"event_type": "model.call_started", "turn_key": turn, "call_role": role, "cost_class": cost,
            "provider_id": "paid:model"}


def test_a_declined_loop_then_the_tool_lane_is_a_turn_that_paid_twice():
    events = [_call("t1", "clean_loop_step"), _call("t1", "clean_loop_step"), _call("t1", "tool_intent"),
              _call("t1", "answer_generation"), {"event_type": "tool_executed", "turn_key": "t1"}]
    (row,) = count_paid_calls(events)
    assert row["paid_calls"] == 4 and row["paid_twice"] is True
    assert row["by_role"] == {"clean_loop_step": 2, "tool_intent": 1, "answer_generation": 1}


def test_a_loop_that_answers_alone_or_a_turn_without_the_loop_paid_once():
    events = [_call("a", "clean_loop_step"), _call("a", "clean_loop_step"),
              _call("b", "tool_intent"), _call("b", "answer_generation"),
              _call("c", "answer_generation", cost="free_local")]
    rows = {row["turn"]: row for row in count_paid_calls(events)}
    assert rows["a"]["paid_twice"] is False and rows["b"]["paid_twice"] is False
    assert "c" not in rows  # a free local call is not counted

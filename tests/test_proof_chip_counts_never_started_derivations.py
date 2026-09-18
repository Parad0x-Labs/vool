"""The proof chip's coverage label counts every derivation the plan held, including the ones that
never started.

Measured on the owner's turn (2026-09-10, build 772256a7): the plan held three quotes and four
derivations; the quotes succeeded, one derivation started and died at the deadline, three never
started. The chip read "VERIFIED · 3/3 lookups · 1 derived step unbound" -- the state word
certifies the committed bytes (integrity), and the coverage label saw only the one derivation
that had a started/completed row. The plan receipt (`conductor_plan_completed`) is the ledger of
every node; a derivation it records as failed is a failed derived step, named as such, distinct
from a derivation that ran but whose operand is not a lookup of this turn (unbound).
"""
from __future__ import annotations

from core.proof_projection import _projection_of_events

PLAN = "conductor-1ba5506b2bdd"


def _node(kind: str, name: str, ok: bool, *, deps=(), failure_code: str = ""):
    return {
        "node_id": f"{PLAN}:{kind}:{name}",
        "operation": kind,
        "ok": ok,
        "depends_on": list(deps),
        "failure_code": failure_code,
        "duration_s": 0.5 if ok else None,
    }


def _events():
    quotes = ("silver", "gold", "ethereum")
    events = []
    for name in quotes:
        events.append({"event_type": "agent_node_started", "node_id": f"{PLAN}:market_quote:{name}", "operation": "market_quote"})
    for name in quotes:
        events.append({
            "event_type": "agent_node_completed", "node_id": f"{PLAN}:market_quote:{name}",
            "operation": "market_quote", "ok": True, "depends_on": [], "duration_s": 0.5,
            "observed": {"source_url": "https://finance.yahoo.com/q", "price": 1.0},
        })
    events.append({"event_type": "agent_node_started", "node_id": f"{PLAN}:quantitative_reasoning:how_much_of_gold", "operation": "quantitative_reasoning"})
    receipt_nodes = [_node("market_quote", name, True) for name in quotes] + [
        _node("quantitative_reasoning", "how_much_silver_can_i_buy_with_1", False, deps=[f"{PLAN}:market_quote:{q}" for q in quotes], failure_code="plan_deadline_expired"),
        _node("quantitative_reasoning", "how_much_gold_can_i_buy_with_1_e", False, deps=[f"{PLAN}:market_quote:{q}" for q in quotes], failure_code="plan_deadline_expired"),
        _node("quantitative_reasoning", "how_much_of_silver_i_can_buy_if_", False, deps=[f"{PLAN}:market_quote:silver"], failure_code="plan_deadline_expired"),
        _node("quantitative_reasoning", "how_much_of_gold", False, deps=[f"{PLAN}:market_quote:gold"], failure_code="plan_deadline_expired"),
    ]
    events.append({"event_type": "conductor_plan_completed", "plan_id": PLAN, "receipt": {"nodes": receipt_nodes, "succeeded_count": 3, "node_count": 7}})
    return events


def test_never_started_derivations_are_counted_as_failed_derived_steps():
    projection = _projection_of_events(_events())
    coverage = projection["coverage"]
    assert coverage["observations"]["total"] == 3 and coverage["observations"]["succeeded"] == 3
    assert coverage["derived"] == {"total": 4, "bound": 0, "unbound": 0, "failed": 4}
    assert coverage["label"] == "3/3 lookups · 4 of 4 derived steps failed"
    states = {row["node_id"].rsplit(":", 1)[-1]: row["state"] for row in projection["observations"] if row["kind"] == "derived"}
    assert states["how_much_of_gold"] == "failed"  # started, never completed before the plan closed
    assert states["how_much_of_silver_i_can_buy_if_"] == "never_started"


def test_a_derivation_that_ran_and_is_bound_keeps_the_previous_label():
    events = _events()[:6]
    events.append({
        "event_type": "agent_node_completed", "node_id": f"{PLAN}:quantitative_reasoning:ratio",
        "operation": "quantitative_reasoning", "ok": True, "duration_s": 0.01,
        "depends_on": [f"{PLAN}:market_quote:silver", f"{PLAN}:market_quote:gold"],
    })
    coverage = _projection_of_events(events)["coverage"]
    assert coverage["label"] == "3/3 lookups"
    assert coverage["derived"] == {"total": 1, "bound": 1, "unbound": 0, "failed": 0}


def test_an_unbound_derivation_is_still_named_unbound_not_failed():
    events = _events()[:6]
    events.append({
        "event_type": "agent_node_completed", "node_id": f"{PLAN}:quantitative_reasoning:ratio",
        "operation": "quantitative_reasoning", "ok": True, "duration_s": 0.01,
        "depends_on": [f"{PLAN}:market_quote:copper"],  # not a lookup of this turn
    })
    coverage = _projection_of_events(events)["coverage"]
    assert coverage["label"] == "3/3 lookups · 1 derived step unbound"

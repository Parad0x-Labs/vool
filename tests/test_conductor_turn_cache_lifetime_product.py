"""Cache lifetime at the seam that actually ships a turn.

`tests/test_planner_artifact_turn_lifetime.py` owns the invariant. This file proves it survives
`VoolAgent._maybe_answer_conductor_turn` -- the real product call, with the real call builders, the
real artifact installation and the real `plan_conductor_turn`. Only the transport is controllable:
a recorder sits at `agent.memory_router._invoke_manifest`, the last seam before a network.

Driven the way the shipped `apps/vool_chat.py` surface drives it: ONE `source_context` dict, reused
for every turn, with the server-owned `turn_id` replaced per message exactly as
`core/agent_runtime/checkpoints.py` replaces it.
"""
from __future__ import annotations

import json

import pytest

TURN_ONE = "Gold price and Silver price and the ratio between them"
TURN_TWO = "weather in Kaunas and customer data"
TURN_THREE = "the weather in Zyrgastan and the price of Gold"

REPLIES = {
    TURN_ONE: {
        "clause_decomposition": json.dumps(
            [
                {"request": "Gold price", "operation": "market_quote", "depends_on": []},
                {"request": "Silver price", "operation": "market_quote", "depends_on": []},
            ]
        ),
        "semantic_proof": json.dumps(
            {
                "frames": [
                    {
                        "frame_id": "f1",
                        "family": "market_quote",
                        "scope": "Gold price",
                        "polarity": "affirmed",
                        "roles": [{"role": "asset_subject", "text": "Gold"}],
                    }
                ]
            }
        ),
    },
    TURN_TWO: {
        "clause_decomposition": json.dumps(
            [
                {"request": "weather in Kaunas", "operation": "weather_lookup", "depends_on": []},
                {"request": "customer data", "operation": "missing_information", "depends_on": []},
            ]
        ),
        "semantic_proof": json.dumps(
            {
                "frames": [
                    {
                        "frame_id": "f1",
                        "family": "weather_lookup",
                        "scope": "weather in Kaunas",
                        "polarity": "affirmed",
                        "roles": [{"role": "location", "text": "Kaunas"}],
                    }
                ]
            }
        ),
    },
    TURN_THREE: {
        "clause_decomposition": json.dumps(
            [
                {
                    "request": "the weather in Zyrgastan",
                    "operation": "weather_lookup",
                    "depends_on": [],
                },
                {"request": "the price of Gold", "operation": "market_quote", "depends_on": []},
            ]
        ),
        "semantic_proof": json.dumps(
            {
                "frames": [
                    {
                        "frame_id": "f1",
                        "family": "weather_lookup",
                        "scope": "the weather in Zyrgastan",
                        "polarity": "affirmed",
                        "roles": [{"role": "location", "text": "Zyrgastan"}],
                    }
                ]
            }
        ),
    },
}


class _Response:
    def __init__(self, text: str) -> None:
        self.output_text = text


class _Transport:
    """Answers by the PROMPT it receives, so a stale reply is visibly another turn's."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.faulting_kinds: set[str] = set()

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        kind = str((request.metadata or {}).get("planner_call_kind") or "")
        self.calls.append(
            {
                "kind": kind,
                "prompt": request.prompt,
                "turn_id": str((source_context or {}).get("turn_id") or ""),
            }
        )
        if kind in self.faulting_kinds:
            raise TimeoutError(f"{kind} read timed out")
        return None, _Response(REPLIES.get(request.prompt, {}).get(kind, "")), None


@pytest.fixture()
def conductor_agent(monkeypatch):
    """A real `VoolAgent` with a controllable transport and nothing else replaced."""
    import core.agent_runtime.audit_routing as audit_routing
    import core.agent_runtime.turn_planner_hook as hook
    from apps.vool_agent import VoolAgent

    monkeypatch.setattr(audit_routing, "select_audit_manifests", lambda *_a, **_k: (["m"], ""))
    monkeypatch.setattr(
        audit_routing, "resolve_routing_mode", lambda _c: type("R", (), {"pinned": False})()
    )
    monkeypatch.setattr(hook, "_unpaid_manifests", lambda _m: ["m"])
    monkeypatch.setattr(hook, "build_pinned_paid_turn_scope", lambda *_a, **_k: None)
    # The artifact registry is keyed by turn id and lives for the PROCESS -- in production every
    # turn id is server-minted and unique. These tests reuse "turn-one" across functions, so
    # without a fresh registry a later test inherits an earlier test's memoized artifact and
    # never reaches the transport (measured: `seen == 0`, identical at 3e1bf797 -- an order
    # dependence of this file, not a product behaviour). Each test starts with an empty registry.
    monkeypatch.setattr(hook, "_ARTIFACTS_BY_TURN", {})

    agent = VoolAgent.__new__(VoolAgent)
    agent.hive_activity_tracker = None
    agent._emit_runtime_event = lambda *_a, **_k: None
    agent._agent_node_emitter = lambda *_a, **_k: None
    agent._execute_tool_intent = lambda *_a, **_k: None
    agent._fast_path_result = lambda **kwargs: dict(kwargs)
    agent.memory_router = _Transport()
    return agent


def _turn(agent, source_context: dict, *, turn_id: str, text: str) -> list[dict]:
    """One product turn. `core/agent_runtime/checkpoints.py` stamps a fresh id per message."""
    from core.agent_runtime.turn_planner_hook import ensure_shared_planner_artifact

    mark = len(agent.memory_router.calls)
    source_context["turn_id"] = turn_id
    # `apps/vool_agent.py:848`, verbatim, on the caller's own long-lived dict.
    ensure_shared_planner_artifact(source_context)
    agent._maybe_answer_conductor_turn(
        effective_input=text,
        raw_input=text,
        session_id="s",
        source_context=source_context,
    )
    return agent.memory_router.calls[mark:]


def test_each_product_turn_asks_its_own_questions(conductor_agent):
    # apps/vool_chat.py:127 -- ONE dict for the life of the process.
    source_context: dict = {"surface": "channel", "platform": "openclaw"}

    for index, text in enumerate((TURN_ONE, TURN_TWO, TURN_THREE), start=1):
        calls = _turn(conductor_agent, source_context, turn_id=f"turn-{index:016x}", text=text)
        kinds = [call["kind"] for call in calls]
        assert kinds == ["clause_decomposition", "semantic_proof"], (
            f"turn {index} made {kinds} -- a turn that asks nothing was served a stale answer"
        )
        assert {call["prompt"] for call in calls} == {text}, (
            f"turn {index} sent another turn's text to the provider"
        )
        assert {call["turn_id"] for call in calls} == {f"turn-{index:016x}"}

    assert len(conductor_agent.memory_router.calls) == 6


def test_a_repeated_message_is_asked_again_rather_than_replayed(conductor_agent):
    source_context: dict = {"surface": "channel", "platform": "openclaw"}
    _turn(conductor_agent, source_context, turn_id="turn-aaaa", text=TURN_ONE)
    again = _turn(conductor_agent, source_context, turn_id="turn-bbbb", text=TURN_ONE)
    assert [call["kind"] for call in again] == ["clause_decomposition", "semantic_proof"]


def test_a_faulting_semantic_call_holds_nothing_after_a_real_product_turn(conductor_agent):
    """C4 at the product boundary: the artifact rides the turn context and can be read off it.

    Downstream this particular contamination would also be caught by the semantic parser refusing
    a clause array -- which is defence in depth and exactly why the product path cannot see it by
    its answer alone. So the assertion is on the cached slot itself, which is where the inheritance
    would happen and where the original P0 lived.
    """
    from core.agent_runtime.turn_planner_hook import _SHARED_PLANNER_ARTIFACT_KEY

    conductor_agent.memory_router.faulting_kinds = {"semantic_proof"}
    source_context: dict = {"surface": "channel", "platform": "openclaw"}
    _turn(conductor_agent, source_context, turn_id="turn-one", text=TURN_ONE)

    artifact = source_context[_SHARED_PLANNER_ARTIFACT_KEY]
    clause_slot = artifact._records["clause_decomposition"].raw
    semantic_slot = artifact._records["semantic_proof"].raw

    assert clause_slot == REPLIES[TURN_ONE]["clause_decomposition"]
    assert semantic_slot == "", "the faulting semantic slot inherited another slot's answer"
    assert semantic_slot != clause_slot


def test_a_semantic_call_that_faults_before_the_transport_still_holds_nothing(
    conductor_agent, monkeypatch
):
    """C4 where it is actually reachable: a fault ABOVE the call builder's own guard.

    `_invoke_once` wraps the manifest invocation, so a transport fault never reaches
    `SharedPlannerArtifact.resolve` as an exception. Manifest SELECTION is not wrapped, so a
    provider-ranking fault does -- and that is the path on which a slot could inherit its
    neighbour's answer. Faulted for the semantic call only, after the decomposition has succeeded.
    """
    import core.agent_runtime.audit_routing as audit_routing
    from core.agent_runtime.turn_planner_hook import _SHARED_PLANNER_ARTIFACT_KEY

    real_select = audit_routing.select_audit_manifests
    seen = {"n": 0}

    def _select(agent, context, routing):
        seen["n"] += 1
        if seen["n"] > 1:  # the decomposition selects first; the semantic call selects second
            raise RuntimeError("provider ranking failed")
        return real_select(agent, context, routing)

    monkeypatch.setattr(audit_routing, "select_audit_manifests", _select)

    source_context: dict = {"surface": "channel", "platform": "openclaw"}
    _turn(conductor_agent, source_context, turn_id="turn-one", text=TURN_ONE)

    artifact = source_context[_SHARED_PLANNER_ARTIFACT_KEY]
    assert seen["n"] > 1, "the semantic call never reached manifest selection"
    assert artifact._records["clause_decomposition"].raw == REPLIES[TURN_ONE]["clause_decomposition"]
    assert artifact._records["semantic_proof"].raw == "", (
        "the faulting semantic slot inherited the decomposition reply"
    )
    assert artifact.failed_kinds() == frozenset({"semantic_proof"})


def test_one_turn_still_spends_at_most_one_call_per_question(conductor_agent):
    """The negative control. Turn scoping must not undo the memoization it sits inside."""
    from core.agent_runtime.turn_planner_hook import (
        build_planner_ask_model,
        build_semantic_proof_ask_model,
    )

    source_context: dict = {"surface": "channel", "platform": "openclaw"}
    calls = _turn(conductor_agent, source_context, turn_id="turn-one", text=TURN_ONE)
    assert len(calls) == 2

    # A second consumer inside the SAME turn -- the generic planner below the conductor -- observes
    # the same artifact through its own copy of the context and must not buy a second generation.
    mark = len(conductor_agent.memory_router.calls)
    build_planner_ask_model(conductor_agent, dict(source_context))("s", TURN_ONE)
    build_semantic_proof_ask_model(conductor_agent, dict(source_context))("s", TURN_ONE)
    assert conductor_agent.memory_router.calls[mark:] == []

"""The REAL call producer: `build_planner_ask_model` and `build_semantic_proof_ask_model`.

This file exists because of a specific measured failure. Every product-tier test injected
`tests/semantic_proposer.py` as the proposer, and none of them constructed the production call
builder -- so the entire real producer was dead while 134 tests passed. In production both stages
shared one unkeyed memoization record, the second call returned the first one's clause array, and
the bounded semantic path proposed nothing on any turn. One provider invocation, a byte-identical
reply, zero frames, green suite.

So the transport is controllable here and NOTHING ELSE IS. The call builder, the cache identity, the
provider schemas, the parsers, the budgets, `parse_proposal` and `validate_frame` are all the real
ones. A stub sits exactly at `agent.memory_router._invoke_manifest`, which is the last seam before
the network, and every assertion below is about what the production machinery did with it.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.turn_planner_hook import (
    PlannerCallKind,
    build_planner_ask_model,
    build_semantic_proof_ask_model,
    ensure_shared_planner_artifact,
)
from core.conductor.requirements import capture_requirements
from core.conductor.semantic_proof import parse_proposal, prove_turn
from core.semantic.canonical_text import CanonicalText

MESSAGE = "Give me the weather in Oslo and Tromso, and what is 137 x 29?"

CLAUSE_REPLY = json.dumps(
    [
        {
            "request": "the weather in Oslo and Tromso",
            "operation": "weather_lookup",
            "depends_on": [],
        },
        {"request": "what is 137 x 29", "operation": "calculation", "depends_on": []},
    ]
)

SEMANTIC_REPLY = json.dumps(
    {
        "frames": [
            {
                "frame_id": "f1",
                "family": "weather_lookup",
                "scope": "the weather in Oslo and Tromso",
                "predicate": "weather",
                "polarity": "affirmed",
                "roles": [
                    {"role": "location", "text": "Oslo"},
                    {"role": "location", "text": "Tromso"},
                ],
            }
        ]
    }
)


class _Response:
    def __init__(self, text: str) -> None:
        self.output_text = text


class _Transport:
    """The one stubbed seam: the last hop before a provider. Records what production asked for."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.calls: list[dict] = []

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        kind = str(request.metadata.get("planner_call_kind") or "")
        self.calls.append(
            {
                "kind": kind,
                "system_prompt": request.system_prompt,
                "prompt": request.prompt,
                "schema": request.contract["json_schema"],
                "max_output_tokens": request.max_output_tokens,
            }
        )
        return None, _Response(self.replies.get(kind, "")), None


class _Agent:
    def __init__(self, transport: _Transport) -> None:
        self.memory_router = transport


@pytest.fixture()
def routing(monkeypatch):
    """Only the manifest selection is stubbed; the request builder below it is production."""
    import core.agent_runtime.audit_routing as audit_routing
    import core.agent_runtime.turn_planner_hook as hook

    monkeypatch.setattr(
        audit_routing, "select_audit_manifests", lambda *_a, **_k: (["manifest"], "")
    )
    monkeypatch.setattr(
        audit_routing, "resolve_routing_mode", lambda _ctx: type("R", (), {"pinned": False})()
    )
    monkeypatch.setattr(hook, "_unpaid_manifests", lambda _m: ["manifest"])


def _drive(replies: dict[str, str]) -> tuple[_Transport, str, str]:
    transport = _Transport(replies)
    agent = _Agent(transport)
    context: dict = {}
    clause_raw = build_planner_ask_model(agent, context)(
        "You split a user's message into the separate requests it makes", MESSAGE
    )
    semantic_raw = build_semantic_proof_ask_model(agent, context)(
        "You label the semantic structure of one message", MESSAGE
    )
    return transport, clause_raw, semantic_raw


# --- the defect itself ---------------------------------------------------------------------------


def test_the_two_stages_are_two_provider_calls(routing):
    """P0. One shared unkeyed record made this ONE call and the semantic path dead."""
    transport, clause_raw, semantic_raw = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    assert len(transport.calls) == 2, "the semantic proposer did not reach a provider"
    assert [call["kind"] for call in transport.calls] == [
        PlannerCallKind.CLAUSE_DECOMPOSITION.value,
        PlannerCallKind.SEMANTIC_PROOF.value,
    ]
    assert clause_raw != semantic_raw, "the second call returned the first one's answer"
    assert clause_raw.startswith("[") and semantic_raw.startswith("{")


def test_each_stage_sends_its_own_system_prompt(routing):
    transport, _clause, _semantic = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    prompts = [call["system_prompt"] for call in transport.calls]
    assert prompts[0] != prompts[1]
    assert "split" in prompts[0]
    assert "label the semantic structure" in prompts[1]


def test_each_stage_sends_its_own_provider_schema(routing):
    """A response for one call type must not validate as another. The schemas differ in KIND."""
    transport, _clause, _semantic = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    clause_schema, semantic_schema = (call["schema"] for call in transport.calls)
    assert clause_schema["type"] == "array"
    assert semantic_schema["type"] == "object"
    assert "frames" in semantic_schema["properties"]
    assert clause_schema != semantic_schema


def test_the_semantic_stage_gets_a_ceiling_sized_for_its_own_output(routing):
    """A clause split is 192 tokens. A frames object with role lists is not."""
    transport, _clause, _semantic = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    clause_call, semantic_call = transport.calls
    assert semantic_call["max_output_tokens"] > clause_call["max_output_tokens"]


# --- cross-contract refusal ----------------------------------------------------------------------


def test_a_clause_array_returned_for_the_semantic_call_is_refused(routing):
    """The exact production failure: the decomposition's answer arriving at the semantic parser."""
    transport, _clause, semantic_raw = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: CLAUSE_REPLY,
        }
    )
    assert len(transport.calls) == 2
    assert semantic_raw == "", "a clause array was accepted as a semantic proposal"


def test_a_semantic_object_returned_for_the_clause_call_is_refused(routing):
    _transport, clause_raw, _semantic = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: SEMANTIC_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    assert clause_raw == "", "a semantic proposal was accepted as a clause split"


# --- the memoization that is still wanted --------------------------------------------------------


def test_each_stage_is_still_asked_at_most_once_per_turn(routing):
    """The sharing was USEFUL and is kept. It simply had no notion of which question was asked."""
    transport = _Transport(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    agent = _Agent(transport)
    context: dict = {}
    for _ in range(3):
        build_planner_ask_model(agent, context)("split", MESSAGE)
        build_semantic_proof_ask_model(agent, context)("label", MESSAGE)
    assert len(transport.calls) == 2, "a cached stage was re-asked"
    artifact = ensure_shared_planner_artifact(context)
    assert artifact.attempted_kinds() == {
        PlannerCallKind.CLAUSE_DECOMPOSITION.value,
        PlannerCallKind.SEMANTIC_PROOF.value,
    }


def test_a_failed_stage_does_not_poison_the_other(routing):
    """A dead semantic call must not make the clause split return empty, or the reverse."""
    _transport, clause_raw, semantic_raw = _drive(
        {PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY}
    )
    assert clause_raw, "the clause split lost its answer to the other stage's failure"
    assert semantic_raw == ""


# --- the real reply reaching the real validator ---------------------------------------------------


def test_the_real_semantic_reply_traverses_the_real_parser_and_validator(routing):
    """END TO END on the producer side: transport -> parser -> validator -> ledger."""
    _transport, _clause, semantic_raw = _drive(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: CLAUSE_REPLY,
            PlannerCallKind.SEMANTIC_PROOF.value: SEMANTIC_REPLY,
        }
    )
    canonical = CanonicalText.of(MESSAGE)
    evidence, abstentions = parse_proposal(semantic_raw, canonical)
    assert evidence, f"the real parser produced nothing: {abstentions}"

    ledger = capture_requirements(MESSAGE, propose=lambda _s, _p: semantic_raw)
    families = sorted(r.family for r in ledger.requirements)
    assert families == ["calculation", "weather_lookup"]
    locations = [
        slot.surface
        for requirement in ledger.of_family("weather_lookup")
        for slot in requirement.slots
    ]
    assert locations == ["Oslo", "Tromso"]


def test_a_turn_with_no_transport_at_all_still_proves_its_closed_syntax(routing):
    """LOCAL-ONLY. No provider, no frames from the bounded path, and no guess."""
    proof = prove_turn(MESSAGE)
    assert [f.family for f in proof.frames] == ["calculation"]

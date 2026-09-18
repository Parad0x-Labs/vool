"""The pre-classification artifact belongs to ONE turn, and a faulting slot holds its own failure.

Two invariants, one object, and each was proven dead by driving something the previous suite never
drove.

TURN LIFETIME. `SharedPlannerArtifact` said "for one user turn" and its identity carried only the
call kind. `apps/vool_agent.py` keeps the caller's `source_context` by reference and installs the
artifact on it; `apps/vool_chat.py` (the shipped Talk_To_VOOL surface) builds one such dict
outside its input loop. Measured live on qwen2.5:7b through `run_once`: three turns, three distinct
server-owned `turn_id`s, ONE artifact object, and turns 2 and 3 made ZERO provider calls because
they were handed turn 1's clause array and turn 1's semantic proposal. The checkpoint front door
does clear the caller's dict, but it repopulates it from a copy that carried the artifact forward.

SEMANTIC FAULT. A slot that raises must hold its own failure. Nothing in the code assigns one slot
from another -- and nothing tested it either, so a mutation making the semantic slot inherit the
decomposition reply on fault passed the whole suite. That mutation is the original P0 in miniature.

Everything below drives the real call builder, the real cache and the real parsers. The only thing
replaced is the transport at `agent.memory_router._invoke_manifest`, the last seam before a network.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _fresh_artifact_registry(monkeypatch):
    """The registry is keyed by turn id and lives for the PROCESS; these tests reuse turn ids
    across functions, so each starts with an empty registry (an order dependence of this file,
    identical at 3e1bf797, not a product behaviour -- production turn ids are unique)."""
    import core.agent_runtime.turn_planner_hook as hook

    monkeypatch.setattr(hook, "_ARTIFACTS_BY_TURN", {})

from core.agent_runtime.turn_planner_hook import (
    _SHARED_PLANNER_ARTIFACT_KEY,
    PlannerCallKind,
    SharedPlannerArtifact,
    build_planner_ask_model,
    build_semantic_proof_ask_model,
    ensure_shared_planner_artifact,
)

TURN_ONE_TEXT = "Gold price and Silver price and the ratio between them"
TURN_TWO_TEXT = "weather in Kaunas and customer data"
TURN_THREE_TEXT = "the weather in Zyrgastan and the price of Gold"


def _clauses(*requests: tuple[str, str]) -> str:
    return json.dumps(
        [{"request": text, "operation": operation, "depends_on": []} for text, operation in requests]
    )


def _proposal(family: str, scope: str, role: str, filler: str) -> str:
    return json.dumps(
        {
            "frames": [
                {
                    "frame_id": "f1",
                    "family": family,
                    "scope": scope,
                    "polarity": "affirmed",
                    "roles": [{"role": role, "text": filler}],
                }
            ]
        }
    )


REPLIES = {
    TURN_ONE_TEXT: {
        "clause_decomposition": _clauses(
            ("Gold price", "market_quote"), ("Silver price", "market_quote")
        ),
        "semantic_proof": _proposal("market_quote", "Gold price", "asset_subject", "Gold"),
    },
    TURN_TWO_TEXT: {
        "clause_decomposition": _clauses(
            ("weather in Kaunas", "weather_lookup"), ("customer data", "missing_information")
        ),
        "semantic_proof": _proposal("weather_lookup", "weather in Kaunas", "location", "Kaunas"),
    },
    TURN_THREE_TEXT: {
        "clause_decomposition": _clauses(
            ("the weather in Zyrgastan", "weather_lookup"), ("the price of Gold", "market_quote")
        ),
        "semantic_proof": _proposal(
            "weather_lookup", "the weather in Zyrgastan", "location", "Zyrgastan"
        ),
    },
}


class _Response:
    def __init__(self, text: str) -> None:
        self.output_text = text


class _Transport:
    """Answers by the PROMPT it is given, so a stale reply is visibly the wrong turn's."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.faulting_kinds: set[str] = set()

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        kind = str((request.metadata or {}).get("planner_call_kind") or "")
        self.calls.append((kind, request.prompt))
        if kind in self.faulting_kinds:
            raise TimeoutError(f"{kind} read timed out")
        return None, _Response(REPLIES.get(request.prompt, {}).get(kind, "")), None

    def kinds_since(self, mark: int) -> list[str]:
        return [kind for kind, _prompt in self.calls[mark:]]


class _Agent:
    def __init__(self, transport: _Transport) -> None:
        self.memory_router = transport


@pytest.fixture()
def routing(monkeypatch):
    """Only manifest selection is replaced; the request builder below it is production."""
    import core.agent_runtime.audit_routing as audit_routing
    import core.agent_runtime.turn_planner_hook as hook

    monkeypatch.setattr(audit_routing, "select_audit_manifests", lambda *_a, **_k: (["m"], ""))
    monkeypatch.setattr(
        audit_routing, "resolve_routing_mode", lambda _c: type("R", (), {"pinned": False})()
    )
    monkeypatch.setattr(hook, "_unpaid_manifests", lambda _m: ["m"])


def _drive_turn(agent, session_context: dict, *, turn_id: str, text: str):
    """`apps/vool_agent.py`: a fresh server-owned turn id, then the two builders on copies."""
    session_context["turn_id"] = turn_id
    artifact = ensure_shared_planner_artifact(session_context)
    clause = build_planner_ask_model(agent, dict(session_context))("clause prompt", text)
    semantic = build_semantic_proof_ask_model(agent, dict(session_context))("semantic prompt", text)
    return artifact, clause, semantic


# --- cross-turn lifetime --------------------------------------------------------------------
def test_three_turns_on_one_source_context_never_share_a_result(routing):
    transport = _Transport()
    agent = _Agent(transport)
    # apps/vool_chat.py:127 -- built ONCE, outside the input loop, mutated in place every turn.
    session_context: dict = {"surface": "channel", "platform": "openclaw"}

    seen_artifacts = []
    for index, text in enumerate((TURN_ONE_TEXT, TURN_TWO_TEXT, TURN_THREE_TEXT), start=1):
        mark = len(transport.calls)
        artifact, clause, semantic = _drive_turn(
            agent, session_context, turn_id=f"turn-{index:032x}", text=text
        )
        seen_artifacts.append(artifact)

        assert transport.kinds_since(mark) == ["clause_decomposition", "semantic_proof"], (
            f"turn {index} did not ask both questions; it was served a cached answer"
        )
        # The transport answers by prompt, so a stale reply is literally another turn's content.
        assert json.loads(clause), f"turn {index} clause split empty"
        assert text in [call[1] for call in transport.calls[mark:]]
        for reply in (clause, semantic):
            assert REPLIES[text]["clause_decomposition"] == clause or reply
        assert clause == REPLIES[text]["clause_decomposition"], f"turn {index} got another turn's split"
        assert semantic == REPLIES[text]["semantic_proof"], f"turn {index} got another turn's proposal"

    assert len({id(a) for a in seen_artifacts}) == 3, "one artifact served three turns"
    assert [a.turn_id for a in seen_artifacts] == [f"turn-{i:032x}" for i in (1, 2, 3)]
    assert len(transport.calls) == 6


def test_the_same_message_twice_is_two_turns(routing):
    """Identity is the server's turn id, never the text: a repeat is new work, not a cache hit."""
    transport = _Transport()
    agent = _Agent(transport)
    session_context: dict = {}

    first, clause_one, _ = _drive_turn(
        agent, session_context, turn_id="turn-aaa", text=TURN_ONE_TEXT
    )
    mark = len(transport.calls)
    second, clause_two, _ = _drive_turn(
        agent, session_context, turn_id="turn-bbb", text=TURN_ONE_TEXT
    )

    assert first is not second
    assert transport.kinds_since(mark) == ["clause_decomposition", "semantic_proof"]
    assert clause_one == clause_two  # same question, asked again, answered again


def test_a_new_turn_id_replaces_the_artifact_on_an_unchanged_context_object(routing):
    """C7: the dict is mutated in place, so its object identity can never be the turn identity."""
    session_context: dict = {"turn_id": "turn-one"}
    first = ensure_shared_planner_artifact(session_context)
    first.resolve(lambda: "TURN ONE SPLIT", kind=PlannerCallKind.CLAUSE_DECOMPOSITION)

    session_context["turn_id"] = "turn-two"
    second = ensure_shared_planner_artifact(session_context)

    assert second is not first
    assert second.attempted_kinds() == frozenset()
    assert session_context[_SHARED_PLANNER_ARTIFACT_KEY] is second


def test_one_turn_still_shares_one_artifact_across_context_copies(routing):
    """The memoization's whole purpose: two planners, one question, one call."""
    session_context: dict = {"turn_id": "turn-one"}
    first = ensure_shared_planner_artifact(session_context)
    # `bind_provider_deadline` hands the conductor a shallow copy; the generic planner below it
    # observes the caller's dict. Both must reach the same object.
    second = ensure_shared_planner_artifact(dict(session_context))
    third = ensure_shared_planner_artifact(session_context)

    assert first is second is third


def test_an_artifact_with_no_turn_identity_is_still_shared(routing):
    """A library caller hands in no turn id. That must not become a call per planner."""
    session_context: dict = {}
    first = ensure_shared_planner_artifact(session_context)
    assert ensure_shared_planner_artifact(session_context) is first
    assert ensure_shared_planner_artifact(dict(session_context)) is first
    assert first.turn_id == ""


# --- semantic fault isolation (the P0 class) ---------------------------------------------------
def test_a_faulting_semantic_call_does_not_inherit_the_decomposition_reply(routing):
    transport = _Transport()
    transport.faulting_kinds = {"semantic_proof"}
    agent = _Agent(transport)
    session_context: dict = {"turn_id": "turn-one"}

    artifact = ensure_shared_planner_artifact(session_context)
    clause = build_planner_ask_model(agent, dict(session_context))("clause prompt", TURN_ONE_TEXT)
    semantic = build_semantic_proof_ask_model(agent, dict(session_context))(
        "semantic prompt", TURN_ONE_TEXT
    )

    assert clause == REPLIES[TURN_ONE_TEXT]["clause_decomposition"]
    assert semantic == "", "the semantic slot inherited another slot's answer"
    assert "Gold" not in semantic
    # Both slots were ASKED, and the faulting one holds nothing rather than its neighbour's answer.
    # `failed_kinds` is empty here on purpose: a provider fault is turned into an empty auxiliary
    # result inside the call builder, so it never reaches `resolve` as an exception. The flag marks
    # a fault that escapes that far, which is the case the test below drives.
    assert artifact.attempted_kinds() == frozenset({"clause_decomposition", "semantic_proof"})
    assert artifact.failed_kinds() == frozenset()


def test_a_faulting_decomposition_does_not_poison_the_semantic_slot(routing):
    """The mirror. One kind's fault is one kind's fault."""
    transport = _Transport()
    transport.faulting_kinds = {"clause_decomposition"}
    agent = _Agent(transport)
    session_context: dict = {"turn_id": "turn-one"}

    clause = build_planner_ask_model(agent, dict(session_context))("clause prompt", TURN_ONE_TEXT)
    semantic = build_semantic_proof_ask_model(agent, dict(session_context))(
        "semantic prompt", TURN_ONE_TEXT
    )

    assert clause == ""
    assert semantic == REPLIES[TURN_ONE_TEXT]["semantic_proof"]


def test_a_faulting_slot_is_cached_as_its_own_failure_not_retried():
    artifact = SharedPlannerArtifact(turn_id="turn-one")
    artifact.resolve(lambda: "CLAUSE ARRAY", kind=PlannerCallKind.CLAUSE_DECOMPOSITION)

    attempts = {"n": 0}

    def _boom() -> str:
        attempts["n"] += 1
        raise TimeoutError("read timed out")

    assert artifact.resolve(_boom, kind=PlannerCallKind.SEMANTIC_PROOF) == ""
    # Asked again in the same turn: still empty, still no second spend, still not the other slot.
    assert artifact.resolve(lambda: "LATE ANSWER", kind=PlannerCallKind.SEMANTIC_PROOF) == ""
    assert attempts["n"] == 1
    assert artifact.failed_kinds() == frozenset({"semantic_proof"})


def test_a_faulting_turn_does_not_poison_the_next_turn(routing):
    """C5: a failed semantic slot is this turn's fact, not the session's."""
    transport = _Transport()
    transport.faulting_kinds = {"semantic_proof"}
    agent = _Agent(transport)
    session_context: dict = {}

    _artifact, _clause, semantic_one = _drive_turn(
        agent, session_context, turn_id="turn-one", text=TURN_ONE_TEXT
    )
    assert semantic_one == ""

    transport.faulting_kinds = set()
    _artifact, _clause, semantic_two = _drive_turn(
        agent, session_context, turn_id="turn-two", text=TURN_TWO_TEXT
    )
    assert semantic_two == REPLIES[TURN_TWO_TEXT]["semantic_proof"]

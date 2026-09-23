"""A message that asks eight things must not be answered by one lane claiming all of it.

THE MEASURED DEFECT (2026-08-12). One message carried eight separate requests:

    what is TRY?                     i meant money TRY
    1000 TRY to USD?                 500 kr to dollars
    <the traveler exchange story>    write a README for Thunder
    where was this file stored?      Create a small Python project called StormWatch ...

The conductor planned seven nodes and three succeeded. The composed partial was discarded because
fewer clauses were served than were unserved, and the turn fell to the front door -- where the
workspace-audit lane claimed the WHOLE message and, the chat being General with the default
workspace binding, answered it with `workspace_audit_default_scope_refused`. Seven of the eight
requests were not refused, not deferred and not named. They were gone.

THE CAUSE, per clause rather than per message
---------------------------------------------
`test_no_clause_of_the_combined_prompt_is_an_audit_request` is the root-cause assertion. Run the
detectors clause by clause and no clause is an audit request; run them on the whole text and it is
one, because the audit VERB comes from the traveler-exchange clause and the FILENAME `app.py` from
the StormWatch clause. The whole-text read also loses claims: `looks_like_agentic_build_request` is
True for the StormWatch clause alone and False for the whole message, and `looks_like_location_ask`
is True for "where was this file stored?" alone and False for the whole message. So the build and
the receipt question were not outvoted -- they were invisible.

THE CONTRACT (`core.agent_runtime.answer_coverage`)
---------------------------------------------------
A claim carries a scope. `whole_turn` may end the turn; `slice` may not. A family holds whole-turn
scope unless some clause is claimed by a DIFFERENT family and not by itself. A blocked lane still
answers its OWN clauses -- the answer is recorded on the turn and the rest routes onward -- so
nothing is dropped and nothing is answered twice.

WHAT IS INJECTED AND WHAT IS NOT
--------------------------------
Nothing that decides a route. The front-door tests drive the real `_handle_turn_frontdoor` on a
real `VoolAgent`. The conductor test injects the PLANNER's clause split -- the same discipline
`tests/test_conductor_multi_intent.py` states, because the runtime's handling of a plan is what is
under test -- and never an answer. The receipt tests write a per-run filename through the real
`workspace.write_file` tool and assert against the receipt that write left, so no assertion here
could have been satisfied by a string written into the code.

ENTITY CONTEXT
--------------
Section 6 is the general form of the same rule. A binding made from an ambiguous token ("kr", "¥",
"dollars") that a later clause contradicts must not survive into reasoning, and the pairings that
cannot both be true are not only monetary -- Toyota + Passat, Apple + Galaxy, France + London. A
clause whose binding is contradicted is reported as needing clarification; the clauses beside it
still answer.

The `test_sabotage_*` tests at the bottom revert each change in turn and name which test dies.
"""

from __future__ import annotations

import json
import tempfile
import uuid

import pytest

from core.agent_runtime.answer_coverage import (
    CLAIM_SCOPE_SLICE,
    CLAIM_SCOPE_WHOLE_TURN,
    COVERAGE_CONTEXT_KEY,
    FAMILY_ASSISTANT_IDENTITY,
    FAMILY_CURRENCY,
    FAMILY_PROJECT_BUILD,
    FAMILY_RECEIPT_LOCATION,
    FAMILY_WORKSPACE_AUDIT,
    claim_may_preempt_turn,
    coverage_for,
    is_mixed_intent_turn,
    slice_families,
    turn_slices,
)
from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request
from core.agent_runtime.workspace_audit import audit_target_in, looks_like_code_audit_request
from core.runtime_execution_tools import execute_runtime_tool

# ---------------------------------------------------------------------------------------------
# The message from the drive, verbatim.
# ---------------------------------------------------------------------------------------------

TRAVELER = (
    'A traveler says: "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came '
    'home with 2,100 kr." Explain whether the exchange was good or bad compared with the normal '
    "exchange rate."
)
STORMWATCH = "Create a small Python project called StormWatch with a README and one app.py file."

COMBINED = "\n".join(
    (
        "what is TRY?",
        "i meant money TRY",
        "1000 TRY to USD?",
        "500 kr to dollars",
        TRAVELER,
        "write a README for Thunder",
        "where was this file stored?",
        STORMWATCH,
    )
)

# The smaller mixed pairs the mission names. Each is two requests no single lane serves.
IDENTITY_CURRENCY_WRITE = "what is your name? 1000 TRY to USD? write a README for Thunder"
CURRENCY_AND_PROJECT = f"500 kr to dollars. {STORMWATCH}"
RECEIPT_AND_CREATION = f"where was this file stored? {STORMWATCH}"
AUDIT_INSIDE_LARGER = "What is TRY? Also audit this project and give me a verdict."

# The negative controls: each of these is ONE request and must keep the lane it always had.
PURE_AUDIT = "Audit this project. Find the security vulnerabilities. Give me a verdict."
PURE_IDENTITY = "what is your name?"
PURE_TRY = "what is TRY?"
PURE_STORMWATCH = STORMWATCH
PURE_RECEIPT = "where was this file stored?"


# =============================================================================================
# 1. The root cause: a claim fused out of two clauses that neither clause makes
# =============================================================================================


def test_no_clause_of_the_combined_prompt_is_an_audit_request() -> None:
    """The measured cause. Eight clauses, not one of them an audit -- and the whole text is one."""
    clauses = [item.text for item in turn_slices(COMBINED)]

    assert len(clauses) == 8
    for clause in clauses:
        assert not looks_like_code_audit_request(clause), f"{clause!r} read as an audit request"

    # The fusion itself, named: the verb comes from one clause and the target from another.
    assert looks_like_code_audit_request(COMBINED) is True
    assert audit_target_in(COMBINED) == "app.py"
    assert "app.py" in STORMWATCH
    assert audit_target_in(TRAVELER) == ""


def test_a_detected_build_still_cannot_preempt_the_other_clauses() -> None:
    """The build detector now finds the imperative inside a mixed message; coverage still scopes it."""
    from core.action_receipt_location import looks_like_location_ask

    assert looks_like_agentic_build_request(STORMWATCH) is True
    assert looks_like_agentic_build_request(COMBINED) is True
    assert claim_may_preempt_turn(COMBINED, FAMILY_PROJECT_BUILD) is False

    assert looks_like_location_ask("where was this file stored?") is True
    assert looks_like_location_ask(COMBINED) is False


def test_the_whole_text_currency_read_mixes_numbers_across_two_requests() -> None:
    """"1000 TRY to USD?" answered with a multiplier that came from a different clause."""
    fused = currency_fast_path(COMBINED)

    assert fused is not None
    assert "2,000 USD" in fused["response"], "the measured cross-clause fusion is gone; retire this"

    # Per clause the same lane refuses to invent a rate, which is the correct answer.
    scoped = currency_fast_path("1000 TRY to USD?")
    assert scoped is not None
    assert "2,000 USD" not in scoped["response"]
    assert "don't have a live FX source" in scoped["response"]


# =============================================================================================
# 2. The coverage contract
# =============================================================================================


def test_every_clause_of_the_combined_prompt_is_read_by_the_family_that_owns_it() -> None:
    families = dict(slice_families(COMBINED))

    assert families["s1"] == (FAMILY_CURRENCY,)
    assert families["s3"] == (FAMILY_CURRENCY,)
    # `in`, as for s6 and s8: a clause may be read by more than one family, and the contract is
    # about WHICH family owns it, never about being read alone. s5 asks to judge an exchange
    # "compared with the normal exchange rate", so the live-info family reads it too -- correctly,
    # since answering it wants a rate. What must not change is the arbitration, asserted below.
    assert FAMILY_CURRENCY in families["s5"]
    assert "file_write" in families["s6"]
    assert families["s7"] == (FAMILY_RECEIPT_LOCATION,)
    assert FAMILY_PROJECT_BUILD in families["s8"]
    # And no clause is read as an audit, so the family that claimed the turn claims nothing.
    assert all(FAMILY_WORKSPACE_AUDIT not in value for value in families.values())


def test_a_second_reader_of_one_clause_changes_no_familys_verdict() -> None:
    """The property the exact-tuple assertion above used to stand in for.

    Exclusivity was incidental -- it held only while the probe registry happened to contain six
    families. What the contract actually owes is that adding a reader to a clause a family already
    owns cannot move that family's scope, and cannot hand whole-turn scope to anyone.
    """
    families = dict(slice_families(COMBINED))
    assert len(families["s5"]) > 1, "fixture no longer exercises a multiply-read clause"

    currency = coverage_for(COMBINED, FAMILY_CURRENCY)
    assert "s5" in currency.consumed
    assert "s5" not in currency.conflicting
    assert currency.scope == CLAIM_SCOPE_SLICE

    # Every family on this eight-request message is a slice claim. None may end the turn.
    for family in (FAMILY_CURRENCY, FAMILY_WORKSPACE_AUDIT, FAMILY_RECEIPT_LOCATION,
                   FAMILY_PROJECT_BUILD):
        assert claim_may_preempt_turn(COMBINED, family) is False


# The registry decides what the arbitration can SEE. `conflicting` counts only clauses claimed by a
# family in `_PROBES`, so while the live-info family was absent, a weather clause read as owed to
# nobody and any other family was free to end the turn on top of it. Measured 2026-08-18, the same
# sentence shape with opposite verdicts:
#
#   "What is your name? Also 1000 TRY to USD?"                -> identity covers_whole_turn=False
#   "What is your name? Also what's the weather in Vilnius?"  -> identity covers_whole_turn=True
#
# The only difference was that currency happened to be registered and weather did not. The turn
# ended on the name; the weather half was never asked. That is the operator's reported failure --
# multi-part turns losing parts -- reproduced at the contract.
MIXED_WITH_AN_UNREGISTERED_HALF = (
    ("What is your name? Also what's the weather in Vilnius?", FAMILY_ASSISTANT_IDENTITY),
    ("Who are you? And what is the current price of gold?", FAMILY_ASSISTANT_IDENTITY),
    ("1000 TRY to USD? Also give me the weather in Rome.", FAMILY_CURRENCY),
    ("Audit this project. Also what's the weather in Berlin?", FAMILY_WORKSPACE_AUDIT),
    ("where was this file stored? also what's the bitcoin price?", FAMILY_RECEIPT_LOCATION),
)


@pytest.mark.parametrize("text,family", MIXED_WITH_AN_UNREGISTERED_HALF)
def test_a_live_info_clause_stops_another_family_ending_the_turn(text: str, family: str) -> None:
    assert claim_may_preempt_turn(text, family) is False


@pytest.mark.parametrize(
    "text,family",
    (
        # One request, one lane: the whole-turn claim these lanes have always had must survive.
        ("What is your name?", FAMILY_ASSISTANT_IDENTITY),
        ("1000 TRY to USD?", FAMILY_CURRENCY),
        # A single-domain message spread over sentences is still one turn. The clause that carries
        # no request of its own must not be mistaken for another family's.
        ("I want to convert money. 1000 TRY to USD?", FAMILY_CURRENCY),
    ),
)
def test_a_single_domain_turn_keeps_its_lane(text: str, family: str) -> None:
    assert claim_may_preempt_turn(text, family) is True


def test_a_retracted_clause_claims_nothing() -> None:
    """A clause the same turn takes back must not collide with the clause that replaces it.

    "Look up the live exchange rate ... ACTUALLY, cancel the live rate lookup. Just assume the rate
    is 150 JPY." reads as a live-info request AND a currency request, so the two families collided
    and the currency lane lost a turn the user plainly left to it.
    """
    text = (
        "Look up the live exchange rate for USD to JPY. Use that live rate to convert $500. "
        "ACTUALLY, cancel the live rate lookup. Just assume the rate is 150 JPY. Convert $500 "
        "using the assumed rate. Output ONLY the final JPY amount as an integer."
    )
    assert claim_may_preempt_turn(text, FAMILY_CURRENCY) is True


def test_sabotage_unregistering_live_info_restores_the_swallowed_weather_clause(monkeypatch) -> None:
    """Revert the registration and the identity lane ends the turn on the weather half again."""
    from core.agent_runtime import answer_coverage as ac

    without = tuple(row for row in ac._PROBES if row[0] != "live_info")
    assert len(without) == len(ac._PROBES) - 1, "live-info probe was not registered; sabotage moot"
    monkeypatch.setattr(ac, "_PROBES", without)
    ac.slice_families.cache_clear()
    try:
        assert ac.claim_may_preempt_turn(
            "What is your name? Also what's the weather in Vilnius?", FAMILY_ASSISTANT_IDENTITY
        ) is True
    finally:
        ac.slice_families.cache_clear()


def test_a_family_that_claims_no_clause_may_not_end_a_mixed_turn() -> None:
    coverage = coverage_for(COMBINED, FAMILY_WORKSPACE_AUDIT)

    assert coverage.scope == CLAIM_SCOPE_SLICE
    assert coverage.consumed == ()
    assert coverage.conflicting == tuple(f"s{n}" for n in range(1, 9))
    assert claim_may_preempt_turn(COMBINED, FAMILY_WORKSPACE_AUDIT) is False


def test_a_family_that_claims_five_of_eight_clauses_still_may_not_end_the_turn() -> None:
    coverage = coverage_for(COMBINED, FAMILY_CURRENCY)

    assert coverage.scope == CLAIM_SCOPE_SLICE
    assert coverage.consumed == ("s1", "s2", "s3", "s4", "s5")
    assert coverage.uncovered == ("s6", "s7", "s8")
    assert claim_may_preempt_turn(COMBINED, FAMILY_CURRENCY) is False


@pytest.mark.parametrize(
    ("text", "family"),
    (
        (PURE_AUDIT, FAMILY_WORKSPACE_AUDIT),
        (PURE_IDENTITY, FAMILY_ASSISTANT_IDENTITY),
        (PURE_TRY, FAMILY_CURRENCY),
        (PURE_STORMWATCH, FAMILY_PROJECT_BUILD),
        (PURE_RECEIPT, FAMILY_RECEIPT_LOCATION),
    ),
)
def test_a_single_domain_message_keeps_whole_turn_scope(text: str, family: str) -> None:
    """The controls. A one-request message -- however many sentences it takes -- is not mixed, and
    the lane that owns it keeps the authority to end the turn."""
    coverage = coverage_for(text, family)

    assert coverage.scope == CLAIM_SCOPE_WHOLE_TURN, text
    assert coverage.conflicting == ()
    assert claim_may_preempt_turn(text, family) is True
    assert is_mixed_intent_turn(text) is False, text


def test_a_multi_sentence_audit_request_is_not_a_mixed_turn() -> None:
    """The sharpest control. Three sentences, one domain: blocking this would be the regression."""
    families = dict(slice_families(PURE_AUDIT))

    assert families["s1"] == (FAMILY_WORKSPACE_AUDIT,)
    # The follow-on sentences claim nothing -- and an unclaimed clause is NOT a conflict, or every
    # request with a trailing instruction would lose its lane.
    assert families["s2"] == ()
    assert families["s3"] == ()
    assert claim_may_preempt_turn(PURE_AUDIT, FAMILY_WORKSPACE_AUDIT) is True


@pytest.mark.parametrize(
    "text", (COMBINED, IDENTITY_CURRENCY_WRITE, CURRENCY_AND_PROJECT, RECEIPT_AND_CREATION, AUDIT_INSIDE_LARGER)
)
def test_every_mixed_message_is_recognised_as_mixed(text: str) -> None:
    assert is_mixed_intent_turn(text) is True, text


def test_a_quoted_sentence_is_not_split_into_two_requests() -> None:
    """The traveler's own quoted sentence ends in a period. Splitting inside it would invent a
    request the user did not write and hand half a story to a lane."""
    story = [item.text for item in turn_slices(TRAVELER)]

    assert len(story) == 1
    assert story[0] == TRAVELER


def test_a_decimal_point_and_a_filename_are_not_clause_boundaries() -> None:
    text = "Convert 1.5 kr to dollars and open app.py"

    assert [item.text for item in turn_slices(text)] == [text]


# =============================================================================================
# 3. The real front door, driven end to end
# =============================================================================================

GENERAL_CHAT_BINDING = {"workspace_binding": "default", "project_id": ""}


@pytest.fixture(scope="module")
def real_agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _drive_front_door(agent, text: str, *, session_id: str, workspace: str, binding: dict | None = None):
    """The real front door. Returns (result, source_context) -- nothing is stubbed."""
    context = {
        "workspace": workspace,
        "workspace_root": workspace,
        "session_id": session_id,
        "operating_mode": "auto",
        "surface": "api",
        # This harness validates clause arbitration, not live retrieval. The shipped API surface
        # is trusted for ambient web access, so veto it explicitly to keep this suite offline and
        # preserve the deterministic no-rate branch under test.
        "allow_remote_fetch": False,
    }
    context.update(binding or {})
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=text,
        effective_input=text,
        normalized_input=text.lower(),
        source_surface="api",
        session_id=session_id,
        source_context=context,
        persona=None,
        interpreted=None,
    )
    return (outcome or {}).get("result"), context


def _write_through_the_real_tool(session_id: str, workspace: str, *, name: str, content: str = "x"):
    result = execute_runtime_tool(
        "workspace.write_file",
        {"path": name, "content": content},
        source_context={"workspace": workspace, "session_id": session_id},
    )
    assert result is not None and result.ok, getattr(result, "response_text", result)
    return result


def test_the_combined_prompt_is_no_longer_answered_by_the_scope_refusal(real_agent) -> None:
    """The regression, on the exact message. Measured before the fix: one refusal, seven silences."""
    session_id = f"mix-combined-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, context = _drive_front_door(
            real_agent, COMBINED, session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    assert result is None, (
        "a single fast path still ended the eight-request turn: "
        f"{result.get('reason') or result.get('route')!r}"
    )
    assert not context.get("workspace_audit_evidence_collected")


def test_the_combined_prompt_accounts_for_every_clause_it_contains(real_agent) -> None:
    """No hidden dropping. Every clause is answered here or named as still owed."""
    session_id = f"mix-cover-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _, context = _drive_front_door(
            real_agent, COMBINED, session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    record = context[COVERAGE_CONTEXT_KEY]
    all_ids = [item["slice_id"] for item in record["slices"]]
    assert all_ids == [f"s{n}" for n in range(1, 9)]

    answered = [slice_id for answer in record["answers"] for slice_id in answer["consumed"]]
    assert sorted(set(answered) | set(record["uncovered"])) == sorted(all_ids)
    assert set(answered) & set(record["uncovered"]) == set()

    families = {answer["family"] for answer in record["answers"]}
    assert FAMILY_CURRENCY in families
    assert FAMILY_RECEIPT_LOCATION in families
    assert all(answer["scope"] == CLAIM_SCOPE_SLICE for answer in record["answers"])
    # The two clauses no deterministic lane owns are the two that route onward -- named, not lost.
    assert record["uncovered"] == ["s6", "s8"]


def test_the_currency_clauses_are_answered_against_their_own_numbers(real_agent) -> None:
    """The blocked lane still does its work, and does it per clause -- so the cross-clause
    "1,000 TRY x 2 = 2,000 USD" that the whole-text read produced is not what is carried forward."""
    session_id = f"mix-currency-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _, context = _drive_front_door(
            real_agent, COMBINED, session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    answer = next(
        item for item in context[COVERAGE_CONTEXT_KEY]["answers"] if item["family"] == FAMILY_CURRENCY
    )
    assert "Turkish lira" in answer["response"]
    assert "2,000 USD" not in answer["response"]
    assert "don't have a live FX source" in answer["response"]


def test_the_deterministic_slice_answers_reach_the_answering_lane_as_typed_evidence(real_agent) -> None:
    """A recorded answer nothing downstream can read is a dropped answer with extra steps."""
    session_id = f"mix-obs-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _, context = _drive_front_door(
            real_agent, COMBINED, session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    observations = [
        item for item in context["runtime_tool_observations"]
        if str(item.get("intent", "")).startswith("turn.slice_answer.")
    ]
    assert observations, "no slice answer reached the observation surface"
    for observation in observations:
        assert observation["schema"] == "tool_observation_v1"
        assert observation["final_answer"] is False
        assert observation["slice_ids"]
        assert observation["slice_text"]
        assert "answer the remaining clauses" in observation["instruction"]


def test_a_currency_question_does_not_swallow_a_project_creation(real_agent) -> None:
    session_id = f"mix-cur-proj-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, context = _drive_front_door(
            real_agent, CURRENCY_AND_PROJECT, session_id=session_id, workspace=workspace,
        )

    assert result is None, f"the currency lane ended the turn: {result}"
    record = context[COVERAGE_CONTEXT_KEY]
    assert [item["family"] for item in record["answers"]] == [FAMILY_CURRENCY]
    assert record["answers"][0]["consumed"] == ["s1"]
    assert record["uncovered"] == ["s2"]


def test_a_receipt_follow_up_does_not_swallow_a_new_creation(real_agent) -> None:
    """The receipt answer is real -- it names the file a real write put on disk -- and it is still
    not the whole turn."""
    session_id = f"mix-receipt-{uuid.uuid4().hex[:10]}"
    name = f"thunder_{uuid.uuid4().hex[:8]}.md"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name=name, content="Thunder is an app.")
        result, context = _drive_front_door(
            real_agent, RECEIPT_AND_CREATION, session_id=session_id, workspace=workspace,
        )

    assert result is None, f"the receipt lane ended the turn: {result}"
    answer = next(
        item for item in context[COVERAGE_CONTEXT_KEY]["answers"]
        if item["family"] == FAMILY_RECEIPT_LOCATION
    )
    assert name in answer["response"], "the receipt answer lost the file the write actually made"
    assert answer["consumed"] == ["s1"]
    assert context[COVERAGE_CONTEXT_KEY]["uncovered"] == ["s2"]


def test_an_audit_phrase_inside_a_larger_prompt_refuses_only_its_own_clause(real_agent) -> None:
    """The General-chat fallback refusal is correct -- for the clause that asked for an audit. It
    must not be the answer to the currency question sitting beside it."""
    session_id = f"mix-audit-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, context = _drive_front_door(
            real_agent, AUDIT_INSIDE_LARGER, session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    assert result is None, f"the audit refusal ended the turn: {result}"
    record = context[COVERAGE_CONTEXT_KEY]
    audit = next(item for item in record["answers"] if item["family"] == FAMILY_WORKSPACE_AUDIT)
    assert audit["reason"] == "workspace_audit_default_scope_refused"
    assert "not bound to a project folder" in audit["response"]
    # Scoped to the clause that asked for it, and the currency clause answered on its own.
    assert audit["consumed"] == ["s2"]
    currency = next(item for item in record["answers"] if item["family"] == FAMILY_CURRENCY)
    assert currency["consumed"] == ["s1"]
    assert "Turkish lira" in currency["response"]


# --- negative controls: every pure form keeps the lane it always had ---------------------------


def test_a_pure_workspace_audit_still_refuses_in_general_chat(real_agent) -> None:
    session_id = f"neg-audit-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, _ = _drive_front_door(
            real_agent, "lets audit this folder?", session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    assert result is not None, "the pure audit request lost its lane"
    assert result["route_reason"] == "workspace_audit_default_scope_refused"
    assert "not bound to a project folder" in result["response"]


def test_a_pure_currency_question_still_answers_on_the_fast_path(real_agent) -> None:
    session_id = f"neg-try-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, _ = _drive_front_door(real_agent, PURE_TRY, session_id=session_id, workspace=workspace)

    assert result is not None, "the pure currency question lost its fast path"
    assert result["route_reason"] == "currency_definition_fast_path"
    assert "Turkish lira" in result["response"]


def test_a_pure_receipt_follow_up_still_answers_from_the_receipt(real_agent) -> None:
    session_id = f"neg-receipt-{uuid.uuid4().hex[:10]}"
    name = f"thunder_{uuid.uuid4().hex[:8]}.md"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name=name)
        result, _ = _drive_front_door(real_agent, PURE_RECEIPT, session_id=session_id, workspace=workspace)

    assert result is not None, "the pure receipt follow-up lost its lane"
    assert result["route"] == "action_receipt_location"
    assert name in result["response"]


def test_a_pure_project_build_still_reaches_the_planner(real_agent) -> None:
    """`{"result": None}` from this gate is the front door standing down for the builder, which is
    the route this request has always taken."""
    session_id = f"neg-build-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, context = _drive_front_door(
            real_agent, PURE_STORMWATCH, session_id=session_id, workspace=workspace,
        )

    assert result is None
    assert COVERAGE_CONTEXT_KEY not in context, "a single-request build recorded a slice answer"
    assert looks_like_agentic_build_request(PURE_STORMWATCH) is True


def test_a_pure_assistant_identity_question_still_answers_locally() -> None:
    from core.web.api.runtime import _assistant_identity_response

    result = _assistant_identity_response(PURE_IDENTITY)

    assert result is not None
    assert result["model_calls"] == 0
    assert claim_may_preempt_turn(PURE_IDENTITY, FAMILY_ASSISTANT_IDENTITY) is True


# =============================================================================================
# 4. The assistant-identity lane, through the real web runtime turn
# =============================================================================================


class _RecordingAgent:
    """Stands in for the agent so "the turn continued past the identity lane" is a fact, not a
    guess. It records the source_context it was handed, which is where the slice answer travels."""

    class ResponseClass:
        GENERIC_CONVERSATION = "generic_conversation"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contexts: list[dict] = []

    def run_once(self, user_text, *, session_id_override=None, source_context=None):
        self.calls.append(user_text)
        self.contexts.append(dict(source_context or {}))
        return {"response": "model answer", "confidence": 0.4, "model_calls": 1}

    def _sanitize_user_chat_text(self, text: str, *, response_class):
        return text


def _run_web_turn(tmp_path, monkeypatch, prompt: str, chat_id: str):
    from core.context_namespace import ensure_chat_namespace
    from core.request_trust import OWNER_LOCAL_KEY
    from core.web.api.runtime import RuntimeServices, run_agent

    agent = _RecordingAgent()
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *a, **k: None)
    ensure_chat_namespace(chat_id, grant_current_receipts=False)

    result = run_agent(
        runtime,
        prompt,
        session_id=chat_id,
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            OWNER_LOCAL_KEY: True,
        },
        workspace_root_provider=lambda: str(tmp_path),
    )
    return result, agent


def test_a_pure_identity_question_ends_the_turn_without_a_model(tmp_path, monkeypatch) -> None:
    """The control for the test below: unmixed, this lane still owns the whole turn."""
    result, agent = _run_web_turn(
        tmp_path, monkeypatch, PURE_IDENTITY, f"identity-pure-{uuid.uuid4().hex[:8]}"
    )

    assert result["route"] == "assistant_identity"
    assert result["model_calls"] == 0
    assert agent.calls == [], "a pure identity question reached the model"


def test_identity_beside_a_currency_question_and_a_write_does_not_end_the_turn(
    tmp_path, monkeypatch
) -> None:
    """The three-request form. Before the fix the name was the entire reply."""
    result, agent = _run_web_turn(
        tmp_path, monkeypatch, IDENTITY_CURRENCY_WRITE, f"identity-mixed-{uuid.uuid4().hex[:8]}"
    )

    assert result.get("route") != "assistant_identity"
    assert agent.calls == [IDENTITY_CURRENCY_WRITE], "the turn never reached a lane that can compose"

    record = agent.contexts[0][COVERAGE_CONTEXT_KEY]
    identity = next(
        item for item in record["answers"] if item["family"] == FAMILY_ASSISTANT_IDENTITY
    )
    assert identity["scope"] == CLAIM_SCOPE_SLICE
    assert identity["consumed"] == ["s1"]
    assert identity["response"], "the identity answer was discarded rather than carried forward"
    # The other two requests are named as still owed, not silently gone.
    assert set(record["uncovered"]) == {"s2", "s3"}


# =============================================================================================
# 5. The conductor: a partial that beats the fallback must ship
# =============================================================================================

# The planner's split of the combined message, in the user's own words (the planner is verified
# against the original text, so an invented word would reject the whole plan). Three clauses reach
# operations that resolve; five do not -- the 3/7-style partial from the drive.
COMBINED_CLAUSES = json.dumps(
    [
        {"request": "what is TRY", "operation": "factual_explanation", "depends_on": []},
        {"request": "i meant money TRY", "operation": "factual_explanation", "depends_on": []},
        {"request": "1000 TRY to USD", "operation": "calculation", "depends_on": []},
        {"request": "500 kr to dollars", "operation": "calculation", "depends_on": []},
        {
            "request": "Explain whether the exchange was good or bad compared with the normal exchange rate",
            "operation": "comparison",
            "depends_on": [],
        },
        {"request": "write a README for Thunder", "operation": "suggestion", "depends_on": []},
        {"request": "where was this file stored", "operation": "lookup", "depends_on": []},
        {
            "request": "Create a small Python project called StormWatch with a README and one app py file",
            "operation": "scaffold",
            "depends_on": [],
        },
    ]
)

# Contract 2026-09-08: the builder is a REGISTERED owner of the StormWatch build, so on the full
# COMBINED message the conductor (which cannot execute a scaffold) stands down and the demand-owned
# executor runs each execution unit through its owner -- served proof in
# validation-logs/wallet-guardrails-20260907/logs/combined_served_r5_members.json. The partial-
# shipping contract is exercised on the same message WITHOUT the build: "write a README for
# Thunder" has no registered owner, so the conductor keeps the turn and names it as owed.
COMBINED_NO_BUILD = "\n".join(COMBINED.split("\n")[:-1])
COMBINED_CLAUSES_NO_BUILD = json.dumps(
    [clause for clause in json.loads(COMBINED_CLAUSES) if clause["operation"] != "scaffold"]
)

# The node generation. Deliberately number-free: `compose_answer` rejects an explanation that
# states a figure the message never established, so a double that tried to smuggle a VALUE in
# would be refused by the same code path a real model's would be.
NODE_GENERATION = "TRY is the currency code for the Turkish lira, used in Turkiye."

# A single-domain message with the same failure ratio. The conductor's old bar must still hold
# here: with a whole-turn lane waiting below, the ordinary path is the better use of the message.
SINGLE_DOMAIN = (
    "Explain whether the exchange was good or bad compared with the normal exchange rate. "
    "Compare the purchasing power of the two currencies and explain which is stronger. "
    "Suggest two ways they could reduce currency-conversion losses."
)
SINGLE_DOMAIN_CLAUSES = json.dumps(
    [
        {
            "request": "Explain whether the exchange was good or bad compared with the normal exchange rate",
            "operation": "factual_explanation",
            "depends_on": [],
        },
        {
            "request": "Compare the purchasing power of the two currencies and explain which is stronger",
            "operation": "comparison",
            "depends_on": [],
        },
        {
            "request": "Suggest two ways they could reduce currency-conversion losses",
            "operation": "suggestion",
            "depends_on": [],
        },
    ]
)


def _drive_conductor(agent, monkeypatch, *, text: str, clauses: str):
    """The real plan, the real graph, the real scheduler. Only the planner split and the node
    generation are injected -- never a value, and never a routing decision."""
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_a, **_k: (lambda _system, _prompt: clauses),
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *_a, **_k: (lambda _system, _prompt: NODE_GENERATION),
    )
    return agent._maybe_answer_conductor_turn(
        effective_input=text,
        raw_input=text,
        session_id=f"cond-{uuid.uuid4().hex[:10]}",
        source_context={"surface": "api", "operating_mode": "auto"},
    )


def test_a_mixed_turn_ships_its_partial_instead_of_falling_back(real_agent, monkeypatch) -> None:
    """The second half of the live defect. Three clauses were served and thrown away, and the
    fallback answered one clause of eight with a refusal nobody asked for."""
    result = _drive_conductor(
        real_agent, monkeypatch, text=COMBINED_NO_BUILD, clauses=COMBINED_CLAUSES_NO_BUILD
    )

    assert result is not None, "the mixed-turn partial was discarded again"
    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert "Turkish lira" in result["response"]
    # Every clause the plan could not serve is NAMED in the reply, which is the whole reason a
    # partial is worth shipping.
    assert "README" in result["response"], "'README' vanished from the composed partial"


def test_a_demand_with_a_registered_owner_sends_the_conductor_to_the_executor(
    real_agent, monkeypatch
) -> None:
    """With the StormWatch build present the conductor holds no executable node for a demand the
    registry says the builder owns. It stands down NAMING that owner, and the demand-owned executor
    (next in `run_once`) runs the build through the builder instead of listing it under "Could not
    be answered" -- served, the build wrote README.md and app.py while the currency units answered
    (logs/combined_served_r5_members.json)."""
    from core.agent_runtime.demand_ownership import demand_coverage

    seen: list = []
    original = real_agent._record_lane_proposal

    def _spy(context, proposal, *args, **kwargs):
        seen.append(proposal)
        return original(context, proposal, *args, **kwargs)

    monkeypatch.setattr(real_agent, "_record_lane_proposal", _spy)
    result = _drive_conductor(real_agent, monkeypatch, text=COMBINED, clauses=COMBINED_CLAUSES)

    assert result is None, "the conductor kept a build a registered lane owns"
    declines = [str(getattr(p, "refusal_reason", "") or "") for p in seen]
    assert any("workspace_write_workflow" in reason and "StormWatch" in reason for reason in declines), declines
    coverage = demand_coverage(COMBINED)
    assert coverage.mixed, "the executor must see a mixed turn to run it"
    assert "workspace_write_workflow" in {lane for lanes in coverage.per_unit_lanes for lane in lanes}


def test_a_mostly_unserved_plan_is_reported_rather_than_handed_on(real_agent, monkeypatch) -> None:
    """The control, restated for the authority that replaced the gate it used to test.

    This asserted `result is None`: a plan whose gaps outnumbered its answers was discarded, on the
    reasoning that a narrower lane would answer better. The F5 cutover deleted that branch, and the
    reason is the measurement that produced it -- on an eight-request message the ratio threw away
    three correct answers and the front door then refused one clause nobody had asked about.
    A turn the conductor planned and could not serve is now CLAIMED and told truthfully.

    What is still controlled is the thing that mattered: the reader is not silently handed to a lane
    that will answer a different question. `is_mixed_intent_turn` no longer participates, which the
    sabotage below asserts directly.
    """
    assert is_mixed_intent_turn(SINGLE_DOMAIN) is False

    result = _drive_conductor(
        real_agent, monkeypatch, text=SINGLE_DOMAIN, clauses=SINGLE_DOMAIN_CLAUSES
    )

    assert result is not None, "a planned turn was silently handed to another lane"
    decision = result["conductor_product_decision"]
    assert decision["claim"] != "not_claimed"
    # Whatever the disposition, it is not a claim that the turn was served.
    assert decision["disposition"] != "fulfilled"
    assert decision["runtime_task_outcome"]["fulfillment_status"] != "fulfilled"
    assert result["response"].strip(), "the reader was told nothing at all"


# =============================================================================================
# 6. Sabotage. Each revert must fail a NAMED test, and the controls must stay green.
# =============================================================================================


# =============================================================================================
# 6. Entity-context consistency: a poisoned binding is one clause's problem
# =============================================================================================

# The pairings named in the review. Each is two entities whose owners disagree, and none of them
# is about currency alone -- that is the point.
IMPOSSIBLE_PAIRINGS = (
    ("convert 500 NOK for my Copenhagen trip", "NOK"),
    ("show me the Toyota Passat", "passat"),
    ("compare the Renault Golf", "golf"),
    ("what is the capital of France, London?", "london"),
    ("is Warsaw the biggest city in Germany?", "warsaw"),
    ("review the Apple Galaxy", "galaxy"),
    ("review the Samsung iPhone", "iphone"),
)


@pytest.mark.parametrize(("clause", "invalidated"), IMPOSSIBLE_PAIRINGS)
def test_an_impossible_pairing_is_reported_as_needing_clarification(clause, invalidated) -> None:
    """Two stated entities whose owners disagree. Nothing here picks a winner for the user."""
    from core.entity_compatibility import Verdict, entity_conflicts

    conflicts = entity_conflicts(clause)

    assert conflicts, f"{clause!r} was read as consistent"
    assert conflicts[0].verdict is Verdict.CLARIFY
    assert conflicts[0].invalidated.surface == invalidated
    assert conflicts[0].needs_clarification is True


@pytest.mark.parametrize(
    ("token_clause", "anchor_clause", "implied", "invalidated_reading"),
    (
        ("500 kr to dollars", "I am in Copenhagen", "DKK", "NOK"),
        ("how much is 200 ¥", "I am in Shanghai", "CNY", "JPY"),
        ("how much is 200 ¥", "I am in Tokyo", "JPY", "CNY"),
    ),
)
def test_a_later_anchor_invalidates_an_earlier_guess_and_names_what_it_implies(
    token_clause, anchor_clause, implied, invalidated_reading
) -> None:
    """The measured shape: an ambiguous token bound early, contradicted by context that arrives
    later. The invalidated reading is named, because everything computed from it is void too."""
    from core.entity_compatibility import Verdict, settlements_across

    settlements = settlements_across(token_clause, anchor_clause)

    assert settlements, f"{anchor_clause!r} settled nothing about {token_clause!r}"
    settlement = settlements[0]
    assert settlement.verdict is Verdict.REBIND
    assert settlement.implied == implied
    assert invalidated_reading in settlement.explanation
    assert "does not hold" in settlement.explanation


@pytest.mark.parametrize(
    "clause",
    (
        # A stated code beside a place is the user's own words, not a guess to be corrected.
        "convert 500 DKK for my Copenhagen trip",
        # One sentence may name several places and several sums without contradicting itself.
        TRAVELER,
        "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore",
        # No entity data for the pair at all -> silence, never a guess.
        "compare the Zyx Qwerty",
        "what is TRY?",
    ),
)
def test_an_ordinary_sentence_is_not_reported_as_an_impossible_pairing(clause) -> None:
    from core.entity_compatibility import entity_conflicts

    assert entity_conflicts(clause) == (), clause


def test_an_anchor_that_names_money_of_its_own_settles_nothing() -> None:
    """The false positive this rule exists to stop. The traveler's Singapore is about the
    traveler's dollars; letting it settle an unrelated earlier "dollars" to SGD would be a poisoned
    binding invented by the code written to prevent one."""
    from core.entity_compatibility import settlements_across

    assert settlements_across("500 kr to dollars", TRAVELER) == ()


def test_a_contradicted_clause_is_reported_without_erasing_the_resolved_ones(real_agent) -> None:
    """The mixed-intent requirement, end to end. One clause's binding is contradicted; the other
    clauses answer, and the contradicted one is reported rather than computed from."""
    text = "500 kr to dollars. I am in Copenhagen. what is TRY?"
    session_id = f"mix-poison-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _, context = _drive_front_door(real_agent, text, session_id=session_id, workspace=workspace)

    record = context[COVERAGE_CONTEXT_KEY]
    poisoned = next(item for item in record["slices"] if item["slice_id"] == "s1")
    assert poisoned["binding_unsafe"] is True
    assert poisoned["conflicts"][0]["implied"] == "DKK"

    contradiction = next(
        item for item in record["answers"] if item["reason"] == "currency_binding_contradicted"
    )
    assert contradiction["consumed"] == ["s1"], "the report claimed clauses it did not serve"
    assert "DKK" in contradiction["response"]
    # Nothing was computed from the reading that no longer holds.
    assert "= " not in contradiction["response"]

    # And the resolved clause still answered, in full.
    answered = next(
        item for item in record["answers"] if item["reason"] == "currency_slice_answers"
    )
    assert "Turkish lira" in answered["response"]
    assert "s3" in answered["consumed"]


def test_the_slice_state_survives_on_every_clause_not_just_the_broken_one(real_agent) -> None:
    """"each slice must carry its own entity-resolution state" -- including the empty state."""
    text = "500 kr to dollars. I am in Copenhagen. what is TRY?"
    session_id = f"mix-state-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _, context = _drive_front_door(real_agent, text, session_id=session_id, workspace=workspace)

    slices = context[COVERAGE_CONTEXT_KEY]["slices"]
    assert [item["slice_id"] for item in slices] == ["s1", "s2", "s3"]
    for item in slices:
        assert "entities" in item and "conflicts" in item and "needs_clarification" in item
    assert slices[2]["binding_unsafe"] is False, "an unrelated clause was marked unsafe"


# =============================================================================================
# 7. The currency lane is asked through ONE call site, with the turn's context
# =============================================================================================


def test_the_front_door_asks_the_currency_lane_in_exactly_one_place() -> None:
    """A per-clause call that bypassed `_currency_reply` would be a second resolver with the same
    name: it would miss this chat's settled codes and answer the same question two ways depending
    on how long the message was."""
    from pathlib import Path

    source = Path(
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")

    # Exactly one invocation, inside the single helper, and none anywhere else in the front door.
    assert source.count("currency_fast_path(text") == 1
    assert source.count("currency_fast_path(effective_input") == 0, (
        "the front door calls the currency lane directly again, bypassing the shared call site"
    )
    helper = source[source.index("def _currency_reply("):source.index("def explicit_model_owns_semantic_turn(")]
    assert "currency_fast_path(text, **extra)" in helper
    # the def, plus the whole-turn, per-clause, and whole-turn-MULTI-clause uses. The multi-clause
    # call is inside the same currency block through the same helper: it exists because
    # "1000 EUR to USD. Also 500 GBP to JPY." owns the turn per slice and the fused-text call
    # answered only the first frame (measured 2026-08-19). The invariant this test protects --
    # nobody calls `currency_fast_path` except the one helper -- is pinned by the two assertions
    # above and is unchanged.
    assert source.count("_currency_reply(") == 4


def test_the_shared_call_site_threads_chat_context_when_the_lane_takes_it(monkeypatch) -> None:
    """The lane's public API carries this chat's already-settled codes. Whether that parameter
    exists yet is the currency case/context work's business; that BOTH paths get it when it does is
    this branch's, and the seam is asserted rather than assumed."""
    import inspect

    from core.agent_runtime import turn_frontdoor
    from core.agent_runtime.fast_paths_currency import currency_fast_path

    seen: list[dict] = []

    def _spy(text, **kwargs):
        seen.append(dict(kwargs))
        return None

    monkeypatch.setattr("core.agent_runtime.fast_paths_currency.currency_fast_path", _spy)
    turn_frontdoor._currency_reply("what is TRY?", session_id="chat-1", source_context={})

    assert len(seen) == 1
    takes_chat_codes = "chat_codes" in inspect.signature(currency_fast_path).parameters
    assert ("chat_codes" in seen[0]) is takes_chat_codes, (
        "the shared call site and the currency lane's own signature disagree about chat context"
    )


def test_nothing_in_the_slice_path_uppercases_the_turn() -> None:
    """Case is evidence. "ALL" is a currency code and "all" is English, and a slice path that
    normalized either would answer the wrong one."""
    from core.agent_runtime.answer_coverage import turn_slices

    text = "what is ALL? and what is all?"
    clauses = [item.text for item in turn_slices(text)]

    assert clauses == ["what is ALL?", "and what is all?"]
    assert any(item.isupper() for item in clauses[0].split())
    assert "ALL" not in clauses[1]


# =============================================================================================
# 8. Sabotage. Each revert must fail a NAMED test, and the controls must stay green.
# =============================================================================================


def _revert_scope_test(monkeypatch) -> None:
    """The single-line revert: every claim reads as whole-turn, exactly as before the fix."""
    import core.agent_runtime.answer_coverage as coverage_module

    monkeypatch.setattr(
        coverage_module,
        "coverage_for",
        lambda text, family, **_kw: coverage_module.ClaimCoverage(
            family=family,
            scope=CLAIM_SCOPE_WHOLE_TURN,
            consumed=(),
            uncovered=(),
            conflicting=(),
        ),
    )


def test_sabotage_removing_the_scope_test_restores_the_whole_turn_swallow(
    real_agent, monkeypatch
) -> None:
    """Reverting the contract puts the measured defect back, in the front door, verbatim."""
    _revert_scope_test(monkeypatch)

    session_id = f"sab-scope-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        result, _ = _drive_front_door(
            real_agent, COMBINED, session_id=session_id, workspace=workspace,
            binding=GENERAL_CHAT_BINDING,
        )

    assert result is not None
    assert result["route_reason"] == "workspace_audit_default_scope_refused", (
        "the reverted front door no longer reproduces the defect; this sabotage proves nothing"
    )


def test_the_conductor_no_longer_consults_the_mixed_turn_heuristic_at_all(
    real_agent, monkeypatch
) -> None:
    """The sabotage inverted, because the thing it sabotaged has no authority left.

    It used to force `is_mixed_intent_turn` False and assert the partial was discarded -- which
    proved the heuristic was load-bearing. It IS no longer load-bearing: shipping is decided by the
    `ProductDecision`, once, and forcing this signal either way must change nothing. A heuristic
    that still moved the outcome would be a fifth authority over one fact, which is what the cutover
    removed.
    """
    monkeypatch.setattr(
        "core.agent_runtime.answer_coverage.is_mixed_intent_turn", lambda _text: False
    )
    forced_false = _drive_conductor(
        real_agent, monkeypatch, text=COMBINED_NO_BUILD, clauses=COMBINED_CLAUSES_NO_BUILD
    )
    monkeypatch.setattr(
        "core.agent_runtime.answer_coverage.is_mixed_intent_turn", lambda _text: True
    )
    forced_true = _drive_conductor(
        real_agent, monkeypatch, text=COMBINED_NO_BUILD, clauses=COMBINED_CLAUSES_NO_BUILD
    )

    assert forced_false is not None and forced_true is not None
    assert forced_false["route_reason"] == forced_true["route_reason"]
    assert (
        forced_false["conductor_product_decision"]["disposition"]
        == forced_true["conductor_product_decision"]["disposition"]
    )


def test_sabotage_a_clause_splitter_that_never_splits_disables_the_whole_contract(monkeypatch) -> None:
    """The contract rests on the split -- and on the fused-slice backstop behind it.

    One clause used to mean one claim, and EVERY lane kept the turn: sabotaging the splitter made
    the wrongness invisible. The 2026-08-28 repair added a second, independent reading for exactly
    this shape -- a single slice that several registered families claim AND whose own text is
    multi-request-shaped is CONTESTED, and no family may preempt it. So the sabotage's symptom
    splits by what the backstop can see:

    - COMBINED is multi-request-shaped and multi-family: the backstop catches the fused slice, the
      preempt is refused, and `conflicting` names the contest -- the splitter's loss degrades to
      slice scope instead of a silently wrong whole-turn claim;
    - a single-family text ("What is your name?") has nothing for the backstop to read, so the
      sabotage there still yields the old whole-turn grant -- which is also the CORRECT verdict
      for that text, meaning a never-splitting splitter is now indistinguishable only where it is
      also harmless.

    `is_mixed_intent_turn` still collapses under the sabotage: it has no backstop, by design.
    """
    import core.agent_runtime.answer_coverage as coverage_module

    monkeypatch.setattr(
        coverage_module,
        "turn_slices",
        lambda text: (
            coverage_module.TurnSlice(slice_id="s1", index=0, text=str(text), start=0, end=len(str(text))),
        ),
    )
    coverage_module.slice_families.cache_clear()
    try:
        fused = coverage_module.coverage_for(COMBINED, FAMILY_WORKSPACE_AUDIT)
        assert fused.scope == coverage_module.CLAIM_SCOPE_SLICE
        assert fused.conflicting, "the contested fused slice must be named, not absorbed"
        assert claim_may_preempt_turn(COMBINED, FAMILY_WORKSPACE_AUDIT) is False
        assert claim_may_preempt_turn("What is your name?", FAMILY_ASSISTANT_IDENTITY) is True
        assert is_mixed_intent_turn(COMBINED) is False
    finally:
        coverage_module.slice_families.cache_clear()


# =============================================================================================
# 7. A blocked live-info lane answers its OWN clauses, like every other blocked lane
# =============================================================================================
#
# Until this was symmetrical, live info was the one family that produced NOTHING when it lost the
# whole turn -- the audit, currency, receipt and identity lanes each serve their own clauses and
# record the result, and this one was skipped outright. Measured live in the shipped app,
# 2026-08-18:
#
#   "who are you? Also whats the WHEATHER in Rome rihgt now?"
#     -> "1. My name is VOOL. 2. I cannot provide real-time weather updates."
#
# The identity half came from its recorded slice answer, carried verbatim exactly as the tool
# observation instructs. The weather half had no recorded fact to carry, so the model said it could
# not serve it -- honest, and still a clause the owning lane never answered.
#
# WHAT IS STUBBED: the network-bound lane call, and only it. The claim decision, the clause split,
# the coverage arbitration and the recording path are all real. The stub also captures the text the
# lane is handed, which is half of what is under test.


def test_a_blocked_live_info_lane_answers_its_own_clause_and_records_it(real_agent, monkeypatch, tmp_path):
    from core.agent_runtime.answer_coverage import claim_may_preempt_turn

    text = "who are you? Also what's the weather in Rome right now?"
    assert claim_may_preempt_turn(text, FAMILY_ASSISTANT_IDENTITY) is False, "fixture is not mixed"

    seen: list[str] = []

    def fake_lane(user_input, *, session_id, source_context, interpretation):
        seen.append(user_input)
        return {"response": "Rome: Sunny, 24 C. Source: [example](https://example.invalid/rome)."}

    monkeypatch.setattr(real_agent, "_maybe_handle_live_info_fast_path", fake_lane)
    result, context = _drive_front_door(
        real_agent, text, session_id=f"cov-live-{uuid.uuid4().hex[:8]}",
        workspace=str(tmp_path), binding=GENERAL_CHAT_BINDING,
    )

    # It was handed ONLY its own clause -- not the identity question beside it.
    assert seen, "the blocked lane was never invoked at all: the defect this test exists for"
    assert "weather" in seen[0].lower()
    assert "who are you" not in seen[0].lower()

    # It did not end the turn on a message it only partly owns.
    assert result is None or "Sunny" not in str(result.get("response") or "")

    # And its answer survived, as an established runtime fact for the rest of the turn.
    observations = [
        item for item in list(context.get("runtime_tool_observations") or [])
        if isinstance(item, dict) and item.get("intent") == "turn.slice_answer.live_info"
    ]
    assert observations, "the lane answered its clause and the answer was thrown away"
    assert "Sunny, 24 C" in str(observations[-1].get("response_preview") or "")
    assert "weather" in str(observations[-1].get("slice_text") or "").lower()


def test_sabotage_skipping_the_blocked_live_info_lane_loses_the_clause(real_agent, monkeypatch, tmp_path):
    """Revert to invoking the lane only on a whole-turn claim, and the weather clause is gone.

    The front door imports these names inside the function, so the patch has to land on the module
    that defines them -- patching the importer would be a no-op and the sabotage would "pass"
    while proving nothing.
    """
    from core.agent_runtime import answer_coverage as ac

    text = "who are you? Also what's the weather in Rome right now?"
    seen: list[str] = []

    def fake_lane(user_input, *, session_id, source_context, interpretation):
        seen.append(user_input)
        return {"response": "Rome: Sunny, 24 C."}

    monkeypatch.setattr(real_agent, "_maybe_handle_live_info_fast_path", fake_lane)

    original = ac.coverage_for

    def whole_turn_only(text_in, family, *, probe=None):
        coverage = original(text_in, family, probe=probe)
        if family == ac.FAMILY_LIVE_INFO and not coverage.covers_whole_turn:
            # The reverted state: a blocked lane is entitled to nothing, consumed clauses included.
            return ac.ClaimCoverage(
                family=coverage.family, scope=coverage.scope, consumed=(),
                uncovered=coverage.uncovered, conflicting=coverage.conflicting,
            )
        return coverage

    monkeypatch.setattr(ac, "coverage_for", whole_turn_only)
    _, context = _drive_front_door(
        real_agent, text, session_id=f"cov-sab-{uuid.uuid4().hex[:8]}",
        workspace=str(tmp_path), binding=GENERAL_CHAT_BINDING,
    )

    assert not seen, "the reverted path still invoked the lane; this sabotage proves nothing"
    assert not [
        item for item in list(context.get("runtime_tool_observations") or [])
        if isinstance(item, dict) and item.get("intent") == "turn.slice_answer.live_info"
    ], "the clause was recorded anyway, so the fix is not what makes the test above pass"


# =============================================================================================
# 8. A contraction must not swallow the rest of the message
# =============================================================================================
#
# Every apostrophe was treated as a quote opening, so the FIRST contraction opened a span that
# never closed and the splitter found no further boundary. Measured 2026-08-18:
#
#   "What is your name? Also what's the weather in Vilnius?"  -> 2 clauses, arbitration works
#   "What's your name? Also what's the weather in Vilnius?"   -> 1 clause, the identity lane ends
#                                                                the turn, the weather half is
#                                                                never asked
#
# One contraction apart. Since that is how the question is ordinarily typed, the per-clause
# arbitration was reachable mainly through the phrasing nobody uses -- including the phrasing this
# suite happened to test.


@pytest.mark.parametrize(
    "text",
    (
        "What's your name? Also what's the weather in Vilnius?",
        "What's your name? Also write a README for Thunder.",
        "Who's there? Also what's the bitcoin price?",
        # A plural possessive ends in an apostrophe with a space after it, which must not open
        # a span either.
        "the cats' toys are missing. Also what's the weather in Rome?",
    ),
)
def test_a_contraction_does_not_collapse_the_clause_split(text: str) -> None:
    assert len(turn_slices(text)) >= 2


@pytest.mark.parametrize(
    "text",
    (
        "What's your name? Also what's the weather in Vilnius?",
        "What's your name? Also write a README for Thunder.",
    ),
)
def test_a_contraction_does_not_hand_a_family_the_whole_turn(text: str) -> None:
    assert claim_may_preempt_turn(text, FAMILY_ASSISTANT_IDENTITY) is False


def test_a_quoted_sentence_is_still_one_clause() -> None:
    """The must-keep control. `_QUOTE_PAIRS` exists so the traveler story is not split into two
    invented requests; repairing the apostrophe must not cost that."""
    assert len(turn_slices(TRAVELER)) == 1


def test_a_leading_apostrophe_still_opens_a_quote() -> None:
    """There is no word character before it for it to belong to, so it is a real quote mark and
    the terminator inside it must not split."""
    text = "He said 'hello there. good day' to me. What's the weather in Rome?"

    assert len(turn_slices(text)) == 2


def test_sabotage_treating_every_apostrophe_as_a_quote_swallows_the_turn(monkeypatch) -> None:
    """Revert the intra-word rule and the contraction case collapses to one clause again.

    The collapse itself is still the pinned symptom -- `turn_slices` fuses the two questions into
    one. What the collapse BUYS the saboteur changed on 2026-08-28: the fused slice is claimed by
    two families and is multi-request-shaped ("Also", two question marks), so the contested-slice
    backstop refuses the whole-turn grant that used to swallow the weather half silently. The
    sabotage stays visible at the slicing layer; it no longer converts into a wrong answer.
    """
    from core.agent_runtime import answer_coverage as ac

    monkeypatch.setattr(ac, "_opens_a_quote", lambda text, index, char: char in ac._QUOTE_PAIRS)
    ac.turn_slices.cache_clear()
    ac.slice_families.cache_clear()
    try:
        assert len(ac.turn_slices("What's your name? Also what's the weather in Vilnius?")) == 1
        fused = ac.coverage_for(
            "What's your name? Also what's the weather in Vilnius?", FAMILY_ASSISTANT_IDENTITY
        )
        assert fused.scope == ac.CLAIM_SCOPE_SLICE
        assert ac.claim_may_preempt_turn(
            "What's your name? Also what's the weather in Vilnius?", FAMILY_ASSISTANT_IDENTITY
        ) is False
    finally:
        ac.turn_slices.cache_clear()
        ac.slice_families.cache_clear()


def test_the_project_build_probe_actually_runs() -> None:
    """It imported a name the module does not export, so every call raised ImportError and
    `slice_families`' `except Exception: continue` swallowed it. The arm had never run."""
    from core.agent_runtime.answer_coverage import _claims_project_build

    assert _claims_project_build("README.md and app.py only") in (True, False)


def test_sabotage_restoring_the_bad_import_makes_the_probe_raise(monkeypatch) -> None:
    from core.agent_runtime import answer_coverage as ac

    def broken(clause: str) -> bool:
        from core.agent_runtime.builder.small_project_plan import (
            small_project_plan,
        )

        return True

    monkeypatch.setattr(ac, "_claims_project_build", broken)
    with pytest.raises(ImportError):
        ac._claims_project_build("README.md and app.py only")

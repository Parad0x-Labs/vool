"""P0 — MIXED DEMANDS MUST EXECUTE, NOT MERELY BE COUNTED.

THE MEASURED DEFECT (base 840a2392, hermetic)
---------------------------------------------
One message, three unrelated demands, a real workspace file on disk:

    "Return exactly the second line of notes.txt. Calculate 37 x 19.
     Decide whether that line describes walking."

served, verbatim:

    "the second line of notes.txt. Calculate 37 x 19. Decide whether that line
     describes walking"

Route `deterministic:workspace_runtime_fast_path`. ZERO of the three demands were
executed — the file read ran and its result was discarded, the arithmetic never
ran, the judgement never ran, and what reached the user was an echo of their own
request. Four independent breaks stacked into that one reply:

1. DECOMPOSITION — `execution_units` merged the arithmetic and the judgement into
   ONE unit, because "Decide" was not a demand head and no verdict verb was: three
   demands, two identities, and the third could never be dispatched or reported.
2. CAPABILITY — the canonical registry implemented per-unit coverage for
   `live_data` and `currency` only. A workspace file read and an arithmetic step
   were executed by real deterministic lanes that DECLARED no coverage, so the
   registry's answer to "who covers this unit?" was nobody, `DemandCoverage.mixed`
   read False, and the demand-owned plan never ran.
3. DISPATCH — the workspace-runtime lane was gated on `is_mixed_intent_turn`, the
   FAMILY reading, while `lane_may_claim_whole_turn` — the catalog's own finalize
   law — already returned False for this exact text. The law existed; the call site
   asked a different question and the lane took the whole turn.
4. OUTPUT BINDING — `apply_exact_response_control` read "Return … exactly …" as a
   literal to echo and OVERWROTE the composed answer at the final boundary. A
   whole-turn claim made by a rewriter that is not a lane and answered to no law.

THE CONTRACT UNDER TEST
-----------------------
Every supported demand executes and the results compose into one answer;
unsupported demands stay explicit rather than vanishing; a lane may end the turn
only when it covers every demand in it; each demand carries a stable identity, the
capability the registry selected for it, whether execution was attempted, and a
typed terminal state.

THE STAND-IN (one, named)
-------------------------
`render_response` plus the router's own `ModelExecutionDecision` are pinned to a
fixed text — the seam `tests/test_demand_ownership_r1e.py` already uses — so the
general-reasoning demand has a deterministic answer. Everything else is real: the
decomposition, the registry, the lanes, the sub-turns, the file on disk, the
arithmetic, the merge. No socket is opened.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from core.agent_runtime.demand_ownership import (
    CAPABILITY_UNSUPPORTED,
    DEMAND_EXECUTED,
    DEMAND_FAILED,
    DEMAND_NOT_ATTEMPTED,
    DemandRecord,
    demand_coverage,
    demand_records,
    execution_units,
    lane_may_claim_whole_turn,
)
from core.lane_registry import CAPABILITY_MODEL_REASONING
from core.turn_contract import TURN_DEMAND_LEDGER_KEY
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

_LINE_ONE = "ALPHA-LINE-ONE the ledger was reconciled"
_LINE_TWO = "BRAVO-LINE-TWO she jogged to the market"
_BRAVO_ONE = "CHARLIE-FIRST the invoice was filed"
_BRAVO_TWO = "DELTA-SECOND he cycled to the office"

_JUDGEMENT = "JUDGEMENT-STAND-IN: jogging is running, not walking."

#: The incident message. Three demands, three families, one turn.
_MIXED = (
    "Return exactly the second line of notes.txt. "
    "Calculate 37 x 19. "
    "Decide whether that line describes walking."
)
#: The same three demands written in a different order — the split must be a
#: property of the demands, not of where they happen to sit.
_MIXED_REORDERED = (
    "Calculate 37 x 19. "
    "Decide whether that line describes walking. "
    "Return exactly the second line of notes.txt."
)
#: Two demands of ONE family. Same capability, two distinct identities.
_SAME_FAMILY = (
    "Return exactly the second line of notes.txt. "
    "Return exactly the first line of bravo.txt."
)


def _model_stand_in(agent, text: str = _JUDGEMENT):
    """Deterministic general answers through the REAL model lane."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model",
        task_hash="p0-mixed-demand",
        provider_id="p0-mixed-demand",
        provider_name="P0 stand-in",
        model_name="p0-mixed-demand",
        output_text=text,
        confidence=0.9,
        trust_score=0.9,
        used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch.object(agent.memory_router, "resolve", return_value=decision)
    )
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return stack


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes.txt").write_text(f"{_LINE_ONE}\n{_LINE_TWO}\n", encoding="utf-8")
    (tmp_path / "bravo.txt").write_text(
        f"{_BRAVO_ONE}\n{_BRAVO_TWO}\n", encoding="utf-8"
    )
    return str(tmp_path)


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-p0mix-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


def _turn(harness: _Harness, text: str, workspace: str) -> tuple[str, dict]:
    """One real turn in a real workspace. Returns (answer, the context it ran under)."""
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with _model_stand_in(harness.agent):
        result = harness.agent.run_once(
            text, source_context=context, session_id_override=harness.session_id
        )
    return str(result.get("response") or ""), context


def _ledger(context: dict) -> list[dict]:
    return [dict(row) for row in (context.get(TURN_DEMAND_LEDGER_KEY) or [])]


# ============================================================ A. single file read


def test_A_a_single_file_read_still_answers_from_disk(harness, workspace):
    """The control. A pure one-demand read keeps its deterministic fast path and
    answers with the file's real bytes — nothing about mixed turns may cost a
    simple read its lane."""
    answer, _context = _turn(harness, "read notes.txt", workspace)

    assert _LINE_ONE in answer and _LINE_TWO in answer, (
        f"the single-demand read no longer answers from disk: {answer!r}"
    )


def test_A_a_single_precise_read_returns_only_the_requested_line(harness, workspace):
    """PRECISION, single demand. "exactly the second line" is a request for ONE
    line. At base the lane read `start_line=1, max_lines=2000` and answered with the
    whole file — the requested content plus content explicitly excluded."""
    answer, _context = _turn(
        harness, "Return exactly the second line of notes.txt.", workspace
    )

    assert _LINE_TWO in answer, f"the requested line was not served: {answer!r}"
    assert _LINE_ONE not in answer, (
        f"a request for exactly one line returned another line beside it: {answer!r}"
    )


# ================================================= B. the three-demand mixed turn


def test_B_the_three_demand_turn_executes_every_supported_demand(harness, workspace):
    """THE INCIDENT. All three demands execute and compose into one answer."""
    answer, _context = _turn(harness, _MIXED, workspace)

    assert _LINE_TWO in answer, (
        f"the file-read demand was not executed: {answer!r}"
    )
    assert "703" in answer, (
        f"the arithmetic demand was not executed — 37 x 19 is 703: {answer!r}"
    )
    assert _JUDGEMENT in answer, (
        f"the judgement demand was not executed: {answer!r}"
    )


def test_B_the_answer_is_not_an_echo_of_the_request(harness, workspace):
    """The base reply was the user's own sentence with 'Return exactly ' removed.
    A turn's answer may never be its request handed back."""
    answer, _context = _turn(harness, _MIXED, workspace)

    assert "Decide whether that line describes walking" not in answer, (
        f"the literal binder echoed the request instead of the executed work: {answer!r}"
    )


def test_B_precision_survives_composition(harness, workspace):
    """The precise read stays precise INSIDE the composed answer: composing three
    results may not quietly widen one of them."""
    answer, _context = _turn(harness, _MIXED, workspace)

    assert _LINE_TWO in answer
    assert _LINE_ONE not in answer, (
        f"composition widened 'exactly the second line' to the whole file: {answer!r}"
    )


def test_B_no_demand_is_executed_twice(harness, workspace):
    """Composition may not duplicate work either. Each executed result appears once."""
    answer, _context = _turn(harness, _MIXED, workspace)

    assert answer.count(_LINE_TWO) == 1, f"the file read was composed twice: {answer!r}"
    assert answer.count("703") == 1, f"the arithmetic was composed twice: {answer!r}"


def test_B_every_demand_gets_identity_capability_attempt_and_state(harness, workspace):
    """The ledger: one row per demand, each carrying a stable identity, the
    capability the REGISTRY selected, an execution attempt, and a typed terminal
    state. Counting demands is not executing them — these rows are what makes the
    difference checkable."""
    _answer, context = _turn(harness, _MIXED, workspace)
    rows = _ledger(context)

    assert [row["demand_id"] for row in rows] == ["u1", "u2", "u3"], rows
    assert [row["capability"] for row in rows] == [
        "workspace_read",
        "arithmetic",
        # NOT "unsupported": the model lane executed this demand, and the ledger
        # records what RAN. See `test_E_*` below for the whole law.
        CAPABILITY_MODEL_REASONING,
    ], rows
    assert all(row["attempted"] for row in rows), rows
    assert [row["terminal_state"] for row in rows] == [DEMAND_EXECUTED] * 3, rows


# ==================================================== C. the demands, reordered


def test_C_reordering_the_demands_changes_nothing(harness, workspace):
    """Same three demands, different order. All three still execute."""
    answer, context = _turn(harness, _MIXED_REORDERED, workspace)

    assert "703" in answer, f"arithmetic lost after reordering: {answer!r}"
    assert _JUDGEMENT in answer, f"judgement lost after reordering: {answer!r}"
    assert _LINE_TWO in answer, f"file read lost after reordering: {answer!r}"
    assert _LINE_ONE not in answer, f"precision lost after reordering: {answer!r}"

    capabilities = sorted(row["capability"] for row in _ledger(context))
    assert capabilities == sorted(
        ["arithmetic", CAPABILITY_MODEL_REASONING, "workspace_read"]
    ), _ledger(context)


# =========================================== D. two same-family demands, distinct


def test_D_two_same_family_demands_get_distinct_identities(harness, workspace):
    """Two file reads are TWO demands, not one. At base they fused into a single
    execution unit — one identity for two requests, so only one could ever be
    dispatched, reported or repaired."""
    units = execution_units(_SAME_FAMILY)

    assert len(units) == 2, f"two same-family demands fused into one unit: {units}"
    assert len({unit_id for unit_id, _text in units}) == 2, units

    coverage = demand_coverage(_SAME_FAMILY)
    assert all(
        "workspace_read_fast_path" in lanes for lanes in coverage.per_unit_lanes
    ), coverage.per_unit_lanes


def test_D_two_same_family_demands_both_execute_with_their_own_precision(
    harness, workspace
):
    """And both run — each with the line window ITS OWN demand asked for. The lane
    may finalize this turn (it covers every unit), which is exactly why collapsing
    the two windows into one shared read would be a lane failing the demand it was
    allowed to claim."""
    answer, _context = _turn(harness, _SAME_FAMILY, workspace)

    assert _LINE_TWO in answer, f"the first demand's line is missing: {answer!r}"
    assert _BRAVO_ONE in answer, f"the second demand's line is missing: {answer!r}"
    assert _LINE_ONE not in answer, f"first file's window widened: {answer!r}"
    assert _BRAVO_TWO not in answer, f"second file's window widened: {answer!r}"


# =============== E. a demand no DETERMINISTIC lane claims, beside supported ones


def test_E_a_demand_no_deterministic_lane_claims_is_served_and_named(
    harness, workspace
):
    """A demand the deterministic registry does not claim is still SERVED by the
    model lane — and the ledger names WHAT SERVED IT.

    This row used to read `capability=unsupported` beside `terminal_state=executed`,
    a SUCCEEDED dispatch and an answer receipt: three statements that cannot all be
    true. "Unsupported" is a claim-time reading; once the turn has run, the ledger
    owes the reader the execution fact.
    """
    _answer, context = _turn(harness, _MIXED, workspace)
    rows = {row["demand_id"]: row for row in _ledger(context)}

    assert rows["u3"]["capability"] == CAPABILITY_MODEL_REASONING, rows["u3"]
    assert rows["u3"]["lane_id"] == "model_lane", rows["u3"]
    assert rows["u3"]["attempted"] is True, rows["u3"]
    assert rows["u3"]["terminal_state"] == DEMAND_EXECUTED, rows["u3"]
    # And NO row claims to have been served by nothing.
    assert not [r for r in rows.values() if r["capability"] == CAPABILITY_UNSUPPORTED], rows


def test_E_a_served_demand_can_never_be_recorded_unsupported():
    """The invariant that makes the old contradiction unwritable, not merely absent."""
    with pytest.raises(ValueError, match="cannot have been served"):
        DemandRecord(
            demand_id="u3",
            request="Decide whether that line describes walking.",
            capability=CAPABILITY_UNSUPPORTED,
            lane_id="",
            attempted=True,
            terminal_state=DEMAND_EXECUTED,
        )
    # The falsifiable direction: a demand nothing served may still be recorded
    # unsupported — that is the honest row, and it must stay writable.
    assert DemandRecord(
        demand_id="u3",
        request="Decide whether that line describes walking.",
        capability=CAPABILITY_UNSUPPORTED,
        lane_id="",
        attempted=True,
        terminal_state=DEMAND_FAILED,
    ).terminal_state == DEMAND_FAILED


def test_E_the_model_served_demand_does_not_suppress_the_deterministic_ones(
    harness, workspace
):
    """Refusing everything because one demand has no owner is the failure this whole
    contract exists to forbid."""
    answer, _context = _turn(harness, _MIXED, workspace)

    assert _LINE_TWO in answer and "703" in answer, (
        f"a model-served sibling suppressed the deterministic demands: {answer!r}"
    )


# ============================================ F. retry / resume, no double work


def test_F_a_repeated_turn_executes_each_demand_once_per_turn(harness, workspace):
    """The same message sent twice runs each demand once PER TURN — never twice
    within one turn, and never carrying a stale result across.

    Dispatches are counted at the planner's own `run_one` seam, so this counts real
    sub-turn executions rather than what the answer happens to look like.
    """
    from core.agent_runtime import turn_planner_hook

    calls: list[str] = []
    real = turn_planner_hook.build_planner_run_one

    def counting(agent, **kwargs):
        inner = real(agent, **kwargs)

        def wrapped(task, done):
            calls.append(str(getattr(task, "request", "")))
            return inner(task, done)

        return wrapped

    with mock.patch.object(
        turn_planner_hook, "build_planner_run_one", side_effect=counting
    ):
        first, _ctx1 = _turn(harness, _MIXED, workspace)
        first_round = list(calls)
        calls.clear()
        second, _ctx2 = _turn(harness, _MIXED, workspace)
        second_round = list(calls)

    assert len(first_round) == 3, f"a turn dispatched {len(first_round)} demands: {first_round}"
    assert len(set(first_round)) == 3, f"a demand was dispatched twice: {first_round}"
    assert len(second_round) == 3, (
        f"the repeated turn dispatched {len(second_round)} demands: {second_round}"
    )
    assert len(set(second_round)) == 3, f"a demand was dispatched twice: {second_round}"
    for answer in (first, second):
        assert answer.count("703") == 1, f"duplicated work in the reply: {answer!r}"


def test_F_a_planned_sub_turn_never_re_plans_itself(harness, workspace):
    """The resume guard. A sub-turn IS one demand; planning it again would execute
    its siblings a second time inside itself."""
    from core.agent_runtime.demand_ownership import demand_coverage as _coverage

    assert _coverage(_MIXED).mixed is True
    # A sub-turn carries `planned_subturn`; the seam must decline outright.
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    context["planned_subturn"] = True
    with _model_stand_in(harness.agent):
        declined = harness.agent._maybe_answer_demand_owned_turn(
            effective_input=_MIXED,
            raw_input=_MIXED,
            session_id=harness.session_id,
            source_context=context,
        )
    assert declined is None, (
        "the demand-owned seam re-planned a turn that was already a planned child"
    )


# ================================================ the laws, asked directly


def test_the_catalog_law_forbids_a_partial_lane_from_ending_the_turn():
    """The whole-turn claim law: a lane covering SOME of the demand may not end a
    turn holding the rest — and neither may the single-unit-limited front-door
    tier the base defect finalized under."""
    for lane_id in (
        "turn_frontdoor_deterministic",
        "workspace_read_fast_path",
        "direct_math_fast_path",
    ):
        assert lane_may_claim_whole_turn(_MIXED, lane_id) is False, lane_id


def test_a_lane_covering_every_demand_may_still_end_the_turn():
    """The falsifiable direction. The law is 'covers everything', not 'never'."""
    assert lane_may_claim_whole_turn(_SAME_FAMILY, "workspace_read_fast_path") is True
    assert lane_may_claim_whole_turn("read notes.txt", "workspace_read_fast_path") is True


def test_the_front_door_itself_refuses_to_finalize_a_mixed_turn(harness, workspace):
    """The DISPATCH seam, driven directly.

    On a whole `run_once` the demand-owned composite claims this turn before the
    main front-door pass is reached, so the front door's own law is not exercised
    there. It is still the seam the base defect finalized through — and there is a
    front-door pass that runs BEFORE the composite seams — so it is pinned here by
    calling it directly. With the law consulted the door declines and the turn
    continues to the lanes that own whole unit plans; sabotage the guard and this
    door answers the three-demand turn with the file line alone, which is the base
    incident exactly.
    """
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    outcome = harness.agent._handle_turn_frontdoor(
        raw_user_input=_MIXED,
        effective_input=_MIXED,
        normalized_input=_MIXED,
        source_surface="openclaw",
        session_id=harness.session_id,
        source_context=context,
        persona=getattr(harness.agent, "persona", None),
        interpreted=None,
    )
    result = (outcome or {}).get("result")
    assert result is None, (
        "a front-door lane finalized a three-demand turn it covers only one unit of: "
        f"{str((result or {}).get('response') or '')!r}"
    )


def test_a_presentation_rider_is_not_promoted_to_its_own_demand():
    """THE CONTROL for the decomposition change. A paragraph that names HOW to
    render work already requested must still merge into the request it renders —
    it executes as nothing on its own. This is what keeps output verbs out of the
    demand-head vocabulary and puts the question to the registry instead."""
    shaped = (
        "weather in Kaunas and Rome. Return the results as exactly two tables: "
        "one for markets (asset, price, 24-hour change, source)."
    )
    units = execution_units(shaped)
    assert len(units) == 1, (
        f"a rendering instruction was promoted to its own executable demand: {units}"
    )


def test_a_shared_context_comparison_is_not_shredded():
    """The other documented control: fragments of ONE comparison whose amounts and
    anchors landed apart stay one execution unit."""
    fused = "Compare 500 kr, $500 and ¥500 for a traveler in Copenhagen and Shanghai"
    assert len(execution_units(fused)) == 1, execution_units(fused)


def test_the_ledger_cannot_record_an_unattempted_demand_as_executed():
    """The ledger's own integrity law. 'Counted, not run' must be unwritable."""
    with pytest.raises(ValueError, match="no execution attempt"):
        DemandRecord(
            demand_id="u1",
            request="anything",
            capability="workspace_read",
            lane_id="workspace_read_fast_path",
            attempted=False,
            terminal_state=DEMAND_EXECUTED,
        )
    with pytest.raises(ValueError, match="not_attempted"):
        DemandRecord(
            demand_id="u1",
            request="anything",
            capability="workspace_read",
            lane_id="workspace_read_fast_path",
            attempted=True,
            terminal_state=DEMAND_NOT_ATTEMPTED,
        )


def test_records_before_dispatch_are_not_attempted():
    """Before the plan runs, every demand is `not_attempted` — never absent, and
    never optimistically `executed`."""
    rows = demand_records(_MIXED)
    assert [row.terminal_state for row in rows] == [DEMAND_NOT_ATTEMPTED] * 3
    assert not any(row.attempted for row in rows)
    assert [row.demand_id for row in rows] == ["u1", "u2", "u3"]


def test_a_literal_echo_may_not_claim_a_turn_that_holds_work():
    """The output-binding law. An explicit literal still binds; a 'literal' that is
    really a request the runtime can execute does not."""
    from core.web.api.response_control import exact_response_target

    assert exact_response_target("respond with exactly PONG") == "PONG"
    assert exact_response_target('return exactly "quoted value"') == "quoted value"
    assert exact_response_target("reply with exactly: R5-CANARY and nothing else") == (
        "R5-CANARY"
    )
    assert exact_response_target(_MIXED) == ""
    assert exact_response_target("Return exactly the second line of notes.txt.") == ""

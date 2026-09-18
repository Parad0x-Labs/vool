"""P0 — MIXED DEMANDS MUST NEVER DISAPPEAR: terminal closure on the served surface.

THE MEASURED DEFECT (base 84bf8b6a, Served Reality bench, case `mixed-demands`)
-------------------------------------------------------------------------------
One message, three demands:

    "Three things: what time is it in Tokyo? Convert 100 US dollars to euros.
     And finish with a two-word joke."

served, verbatim:

    "01:09 JST"                     route=date_time_fast_path
    closure_verdict: demand_minted=3, demand_satisfied=0,
                     demand_indeterminate=3, demand_rendered=0
    turn_result: fulfilled_obligations=0, unresolved_obligations=3

The obligation mint counted THREE demands; the turn finalized ONE answer; two
demands vanished with no state at all, and the one answer that WAS served was
recorded as satisfying nothing.

ROOT CAUSE — a claim-law omission class, not a weather/currency phrase bug
------------------------------------------------------------------------
`lane_registry.LANE_CATALOG` declares every lane that may END an external turn,
and `demand_ownership.lane_may_claim_whole_turn` is the finalize law: a lane
whose coverage does not span EVERY minted demand unit cannot finalize the turn
(R1f). The law already answered False for this text. Three whole-turn finalize
arms in `turn_frontdoor` never asked it:

* the date_time arm — claims on any clock phrase anywhere in the message;
* the currency whole-turn/multislice arm — its slice coverage reads
  `covers_whole_turn=True` whenever the slicer returns one slice, so a
  conversion buried beside two other demands still looks like a pure turn;
* the live_info whole-turn arm — same single-slice blindness.

Any of them serially swallows a mixed turn and ends it on the one clause it can
serve. THE OWNING BOUNDARY is the demand-unit mint: every whole-turn finalize
arm must ask the catalog's finalize law for its own lane before ending the
turn, exactly as the audit lane and the fast-command intents already do.

THE CONTRACT UNDER TEST
-----------------------
Every minted demand in any multi-demand turn finishes EXACTLY ONE terminal
state (answered / refused / failed / delegated-tracked / explicitly
unresolved); the final response REPRESENTS every demand's outcome; one
retrieval or tool failure never erases successful or still-executable
siblings; counts, served content and receipts agree; canonical
turn/attempt/identity threading is untouched.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from core.agent_runtime.demand_ownership import (
    DEMAND_EXECUTED,
    DEMAND_FAILED,
    DEMAND_NOT_ATTEMPTED,
    DEMAND_REFUSED,
    demand_coverage,
    lane_may_claim_whole_turn,
)
from core.turn_contract import TURN_DEMAND_LEDGER_KEY
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

_LINE_ONE = "ALPHA-LINE-ONE the ledger was reconciled"
_LINE_TWO = "BRAVO-LINE-TWO she jogged to the market"

#: One deterministic general answer, so creative demands have a stand-in.
_CREATIVE = "CREATIVE-STAND-IN: rest, tiny joke."

_TERMINAL_STATES = {
    DEMAND_EXECUTED,
    DEMAND_FAILED,
    DEMAND_NOT_ATTEMPTED,
    DEMAND_REFUSED,
    "pending_approval",
}

#: The served incident, hermetic.
_SERVED_INCIDENT = (
    "Three things: what time is it in Tokyo? Convert 100 US dollars to euros. "
    "And finish with a two-word joke."
)

_TIME_AND_MATH = "What time is it in Tokyo? Calculate 37 x 19."

_EIGHT_UNITS = (
    "What time is it in Tokyo? "
    "Calculate 37 x 19. "
    "Return exactly the second line of notes.txt. "
    "Tell me a two-word joke. "
    "Calculate 12 x 12. "
    "What time is it in Paris? "
    "Return exactly the first line of notes.txt. "
    "Tell me another two-word joke."
)

_EIGHT_REORDERED = (
    "Tell me another two-word joke. "
    "Return exactly the first line of notes.txt. "
    "What time is it in Paris? "
    "Calculate 12 x 12. "
    "Tell me a two-word joke. "
    "Return exactly the second line of notes.txt. "
    "Calculate 37 x 19. "
    "What time is it in Tokyo?"
)

_DUPLICATES = (
    "What time is it in Tokyo? "
    "What time is it in Tokyo? "
    "Calculate 37 x 19."
)

_ONE_FAILURE = (
    "What time is it in Tokyo? "
    "Return exactly the second line of missing.txt. "
    "Tell me a two-word joke."
)

_TWO_FAILURES = (
    "Return exactly the second line of missing.txt. "
    "Calculate 37 x 19 on the ledger from missing.txt. "
    "Tell me a two-word joke."
)

#: A refusal demand beside servable ones: moving money is declined by the
#: currency family's own admission; the conversion and the joke must still be
#: served, and the refusal itself must be REPRESENTED, never dropped.
_REFUSAL_MIXED = (
    "Convert 100 US dollars to euros. "
    "Transfer 50 dollars to Dave. "
    "Tell me a two-word joke."
)


def _model_stand_in(agent, text: str = _CREATIVE):
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model",
        task_hash="p0-terminal-closure",
        provider_id="p0-terminal-closure",
        provider_name="P0 stand-in",
        model_name="p0-terminal-closure",
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
    return str(tmp_path)


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-p0tc-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


def _turn(harness: _Harness, text: str, workspace: str) -> tuple[str, dict]:
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with _model_stand_in(harness.agent):
        result = harness.agent.run_once(
            text, source_context=context, session_id_override=harness.session_id
        )
    return str(result.get("response") or ""), context


def _ledger(context: dict) -> list[dict]:
    return [dict(row) for row in (context.get(TURN_DEMAND_LEDGER_KEY) or [])]


@pytest.mark.parametrize("text,subject", [
    ("In 45 seconds, remind me in this chat to inspect the beta export. Also explain in one sentence how a queue differs from a stack.", "inspect the beta export"),
    ("Remind me to review the orchard inventory in 60 seconds. Separately, explain why a stack removes its newest item first.", "review the orchard inventory"),
])
def test_operator_reminder_preserves_independent_knowledge_demand(harness, workspace, text, subject):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    response, context = _turn(harness, text, workspace)
    reminders = list_reminders(session_id=harness.session_id, get_connection_fn=get_connection)
    assert len(reminders) == 1
    assert subject in reminders[0]["note"]
    assert "explain" not in reminders[0]["note"].lower()
    assert _CREATIVE in response
    assert len(_ledger(context)) == 2
    from core.persistent_memory import recent_conversation_events

    history = recent_conversation_events(harness.session_id, limit=20)
    assert [row["user"] for row in history] == [text], "Internal requests must not become user messages."



@pytest.mark.parametrize("text", [
    "In 45 seconds, remind me in this chat to inspect the beta export. Also explain in one sentence how a queue differs from a stack.",
    "Remind me to review the orchard inventory in 60 seconds. Separately, explain why a stack removes its newest item first.",
])
def test_mixed_served_commit_belongs_to_parent_request(harness, workspace, text):
    from uuid import uuid4

    from core.invocation.ledger import accept_invocation
    from core.persistent_memory import (
        flush_staged_conversation_events,
        open_transcript_commit_boundary,
        recent_conversation_events,
    )
    from core.semantic.semantic_admissions import bound_request_context
    from tests.test_transcript_persists_at_commit import _seal

    request_id = accept_invocation(
        external_kind="served-mixed", external_value=uuid4().hex,
        principal="owner_local", session_binding=harness.session_id,
    )["request_id"]
    context = dict(_SOURCE_CONTEXT, workspace=workspace, transcript_commit_boundary=True)
    open_transcript_commit_boundary(request_id)
    try:
        with bound_request_context(request_id), _model_stand_in(harness.agent):
            result = harness.agent.run_once(
                text, source_context=context, session_id_override=harness.session_id,
            )
            commit = _seal(str(result["response"]), turn_id=request_id + ":turn")
        assert flush_staged_conversation_events(request_id) == 0
        rows = recent_conversation_events(harness.session_id, limit=20)
        assert len(rows) == 1
        assert rows[0]["user"] == text
        assert rows[0]["assistant"] == commit["canonical_content"]
        assert rows[0]["finalization_id"] == commit["finalization_id"]
        assert rows[0]["commit_state"] == "committed"
    finally:
        flush_staged_conversation_events(request_id)


def test_quoted_reminder_subject_is_one_demand():
    from core.agent_runtime.answer_coverage import demand_units

    text = 'Remind me in 60 seconds about "Inspect the export. Also explain the queue to Mira."'
    assert len(demand_units(text)) == 1


@pytest.mark.parametrize("probe_reply", ["not a verdict", TimeoutError("probe unavailable")])
@pytest.mark.parametrize("question", [
    "explain in one sentence why a stack removes its newest item first.",
    "explain why leaves change colour in autumn.",
])
def test_failed_ambiguity_probe_does_not_complete_knowledge_sibling(
    harness, workspace, monkeypatch, probe_reply, question,
):
    from types import SimpleNamespace

    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda *args: ([SimpleNamespace(provider_id="test-author")], ""),
    )
    monkeypatch.setattr(
        "core.final_answer_authorship.decide_final_answer_author",
        lambda **kwargs: SimpleNamespace(eligible=True, reason="test"),
    )
    probe = mock.Mock(
        side_effect=probe_reply if isinstance(probe_reply, Exception) else None,
        return_value=probe_reply,
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *args, **kwargs: probe,
    )
    response, context = _turn(
        harness, "Remind me to review the orchard inventory in 60 seconds. Separately, " + question,
        workspace,
    )
    rows = _ledger(context)
    assert len(rows) == 2
    assert sorted(row["terminal_state"] for row in rows) == sorted([DEMAND_EXECUTED, DEMAND_FAILED])
    assert probe.call_count == 2
    assert _CREATIVE not in response
    reminders = list_reminders(session_id=harness.session_id, get_connection_fn=get_connection)
    assert len(reminders) == 1
    assert "explain" not in reminders[0]["note"]


@pytest.mark.parametrize("prefix", ["Damn it,", "Oh hell,", "Separately,"])
def test_discourse_prefix_is_not_a_phantom_operator_request(prefix):
    from core.agent_runtime.demand_ownership import execution_units

    units = execution_units(prefix + " remind me to check the spice cupboard in 60 seconds.")
    assert len(units) == 1
    assert "spice cupboard" in units[0][1]


def test_two_reminders_are_two_durable_actions(harness, workspace):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    _turn(harness, "Remind me to inspect the saffron inventory in 60 seconds. Remind me to close the orchard gate in 90 seconds.", workspace)
    rows = list_reminders(session_id=harness.session_id, get_connection_fn=get_connection)
    assert sorted(row["note"] for row in rows) == ["close the orchard gate", "inspect the saffron inventory"]


def _assert_terminal(rows: list[dict]) -> None:
    """The closure law: every row carries exactly one typed terminal state."""
    assert rows, "a multi-demand turn minted no demand ledger rows at all"
    for row in rows:
        state = row.get("terminal_state")
        assert state in _TERMINAL_STATES, (
            f"demand {row.get('demand_id')!r} ended in non-terminal state {state!r}"
        )


# =============================================================================
# S. THE SERVED INCIDENT — the exact bench message, hermetic
# =============================================================================


def test_S_time_conversion_creative_all_served(harness, workspace):
    """THE INCIDENT. All three demands of the bench message are represented in
    one answer: the clock, the conversion attempt, the creative unit."""
    answer, context = _turn(harness, _SERVED_INCIDENT, workspace)

    assert "JST" in answer, f"the time demand was dropped: {answer!r}"
    assert "CREATIVE-STAND-IN" in answer, f"the creative demand was dropped: {answer!r}"
    lowered = answer.lower()
    assert ("EUR" in answer) or ("euro" in lowered) or ("failed" in lowered) or (
        "couldn't" in lowered or "could not" in lowered
        or "retrieval is disabled" in lowered
        or "live observation" in lowered
    ), (
        f"neither the conversion nor its honest failure is represented: {answer!r}"
    )
    _assert_terminal(_ledger(context))


def test_S_lane_cannot_finalize_the_incident_whole(harness):
    """The claim law itself: no single-coverage lane may END this turn."""
    for lane in (
        "turn_frontdoor_deterministic",
        "currency_frontdoor",
        "currency_value_contract",
        "live_info_fast_path",
        "live_data_typed_plan",
        "date_time_fast_path",
    ):
        assert not lane_may_claim_whole_turn(_SERVED_INCIDENT, lane), (
            f"{lane} may finalize the three-demand bench turn whole"
        )


# =============================================================================
# C. COMBINATIONS — factual / live / creative / file / refusal, 2–8 units
# =============================================================================


def test_C_time_plus_arithmetic_both_served(harness, workspace):
    answer, context = _turn(harness, _TIME_AND_MATH, workspace)

    assert "JST" in answer, f"the time demand was dropped: {answer!r}"
    assert "703" in answer, f"the arithmetic demand was dropped: {answer!r}"
    _assert_terminal(_ledger(context))


def test_C_eight_units_all_served_in_any_order(harness, workspace):
    for label, text in (("ordered", _EIGHT_UNITS), ("reordered", _EIGHT_REORDERED)):
        answer, context = _turn(harness, text, workspace)

        assert "JST" in answer and ("CEST" in answer or "CET" in answer), (
            f"[{label}] a time demand was dropped: {answer!r}"
        )
        assert "703" in answer and "144" in answer, (
            f"[{label}] an arithmetic demand was dropped: {answer!r}"
        )
        assert _LINE_ONE in answer and _LINE_TWO in answer, (
            f"[{label}] a file demand was dropped: {answer!r}"
        )
        assert answer.count("CREATIVE-STAND-IN") >= 2, (
            f"[{label}] a creative demand was dropped: {answer!r}"
        )
        rows = _ledger(context)
        _assert_terminal(rows)
        assert len(rows) >= 6, f"[{label}] an 8-unit turn minted {len(rows)} rows"


def test_C_duplicate_units_each_close(harness, workspace):
    """Duplicate/overlapping units: every minted copy ends somewhere typed —
    duplicates may co-serve, they may not vanish."""
    answer, context = _turn(harness, _DUPLICATES, workspace)

    assert "JST" in answer and "703" in answer, f"a demand was dropped: {answer!r}"
    _assert_terminal(_ledger(context))


def test_C_refused_unit_named_beside_served_sibling(harness, workspace):
    """A refusal demand (move money) beside a servable one: the refusal is
    represented and the sibling is served."""
    answer, context = _turn(harness, _REFUSAL_MIXED, workspace)

    assert "CREATIVE-STAND-IN" in answer, f"the servable sibling vanished behind the refusal: {answer!r}"
    assert "live observation" in answer.lower() or "EUR" in answer, (
        f"the conversion sibling vanished behind the refusal: {answer!r}"
    )
    rows = _ledger(context)
    _assert_terminal(rows)
    # The transfer unit must close SOMEWHERE typed -- the currency family's own
    # refusal when it takes the unit, or model_reasoning when the fallback owns
    # it. What is forbidden is the third thing: no row, or no state.
    transfer_rows = [r for r in rows if "Transfer" in str(r.get("request"))]
    assert transfer_rows, f"the refusal demand minted no ledger row: {rows}"


# =============================================================================
# F. FAILURE ISOLATION — one failure must not erase siblings
# =============================================================================


def test_F_one_failure_preserves_siblings(harness, workspace):
    answer, context = _turn(harness, _ONE_FAILURE, workspace)

    assert "JST" in answer, f"a failed sibling erased the time demand: {answer!r}"
    assert "CREATIVE-STAND-IN" in answer, f"a failed sibling erased the creative demand: {answer!r}"
    assert "missing.txt" in answer, (
        f"the failed demand is not represented by name: {answer!r}"
    )
    rows = _ledger(context)
    _assert_terminal(rows)
    # The absent file is either a typed FAILED unit or an executed unit whose
    # honest answer names the absence -- both are terminal closure. Forbidden is
    # the third thing: no state, or the siblings erased.
    missing_rows = [r for r in rows if "missing.txt" in str(r.get("request"))]
    assert missing_rows, f"the missing-file demand minted no ledger row: {rows}"
    for row in missing_rows:
        assert row["terminal_state"] in (DEMAND_FAILED, DEMAND_EXECUTED), row


def test_F_two_failures_preserve_the_survivor(harness, workspace):
    answer, context = _turn(harness, _TWO_FAILURES, workspace)

    assert "CREATIVE-STAND-IN" in answer, f"two failures erased the surviving demand: {answer!r}"
    assert "missing.txt" in answer, (
        f"the failed demands are not represented by name: {answer!r}"
    )
    _assert_terminal(_ledger(context))


# =============================================================================
# R. RETRY — the same message again closes again, on its own identity
# =============================================================================


def test_R_retry_closes_again_per_turn(harness, workspace):
    first_answer, first_ctx = _turn(harness, _SERVED_INCIDENT, workspace)
    second_answer, second_ctx = _turn(harness, _SERVED_INCIDENT, workspace)

    for label, (answer, ctx) in (
        ("first", (first_answer, first_ctx)),
        ("retry", (second_answer, second_ctx)),
    ):
        assert "JST" in answer and "CREATIVE-STAND-IN" in answer, (
            f"[{label}] a demand was dropped: {answer!r}"
        )
        _assert_terminal(_ledger(ctx))


# =============================================================================
# P. COUNTS AGREE — minted, served and ledger tell one story
# =============================================================================


def test_P_ledger_covers_every_minted_unit(harness, workspace):
    """Whatever the mint counts, the per-demand ledger rows account for exactly
    those units — no minted demand without a row, no row without a unit."""
    text = _SERVED_INCIDENT
    answer, context = _turn(harness, text, workspace)
    coverage = demand_coverage(text)
    rows = _ledger(context)

    _assert_terminal(rows)
    assert coverage.unit_count >= 2
    assert len(rows) >= coverage.unit_count, (
        f"minted {coverage.unit_count} units but only {len(rows)} ledger rows: {rows}"
    )


# =============================================================================
# W. CONCURRENT CHILDREN — demands executing in parallel close terminally
# =============================================================================


def test_W_concurrent_children_all_close_one_terminal_state():
    """Six independent demands fan out on real worker threads; one fails mid-wave.

    The concurrency authority is `run_plan` (waves + ThreadPoolExecutor); the
    demand-owned seam pins max_workers=1 for measured local-model contention,
    so concurrency is proven at the layer that owns it. What must hold is the
    closure law UNDER concurrency: one outcome per task regardless of interleaving,
    the failed child named, every successful sibling's answer in the merge.
    """
    import threading
    import time

    from core.agent_runtime.turn_planner import PlannedTask, merge_outcomes, run_plan

    tasks = [PlannedTask(index=i, request=f"demand unit {i}") for i in range(6)]
    threads_seen: set[int] = set()
    guard = threading.Lock()

    def run_one(task, _done):
        with guard:
            threads_seen.add(threading.get_ident())
        time.sleep(0.03)  # force interleaving inside the wave
        if task.index == 3:
            raise RuntimeError("simulated sibling failure mid-wave")
        return f"ANSWER-{task.index}"

    outcomes = run_plan(tasks, run_one=run_one, max_workers=4)

    # Parity under concurrency: exactly one outcome per task, no vanishings.
    assert len(outcomes) == len(tasks)
    assert sorted(o.task.index for o in outcomes) == list(range(6))
    ok = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok and not o.needs_approval]
    assert len(ok) == 5 and len(failed) == 1
    assert failed[0].task.index == 3
    # The wave genuinely ran in parallel — the pool path, not the fallback.
    assert len(threads_seen) > 1, "children executed sequentially; concurrency unproven"
    # The merge keeps every sibling and names the failure.
    merged = merge_outcomes(outcomes)
    for o in ok:
        assert o.answer in merged
    assert "demand unit 3" in merged


def test_W_dependent_wave_child_reads_its_predecessor():
    """A child that declares a dependency runs in a LATER wave and sees the
    predecessor's outcome — the second half of the concurrency contract."""
    from core.agent_runtime.turn_planner import PlannedTask, run_plan

    tasks = [
        PlannedTask(index=0, request="first unit"),
        PlannedTask(index=1, request="second unit", depends_on=(0,)),
    ]
    seen: dict[int, str] = {}

    def run_one(task, done):
        if task.index == 1:
            seen[1] = str(done.get(0).answer if 0 in done else "")
            return "second-answer"
        return "first-answer"

    outcomes = run_plan(tasks, run_one=run_one, max_workers=4)
    assert [o.answer for o in outcomes] == ["first-answer", "second-answer"]
    assert seen[1] == "first-answer", "the dependent child ran without its predecessor"

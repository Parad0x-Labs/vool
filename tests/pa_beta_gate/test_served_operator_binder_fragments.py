"""A fragment an operator request does not read is dispatched as its own unit, through the served turn.

Measured in-process at 1d58a641 (tests/test_operator_binder_binds_only_what_the_request_reads.py): the operator binder
bound every fragment of a text the operator parser claimed, so 'open my Apple note "Plan", Ukraine situation' was ONE
execution unit the operator lane could end alone, and the topic reached no lane. The binder now binds only the
fragments the operator lane's own reading uses, so a folder the read names still rides it.

Every case is a real served turn (`VoolAgent.run_once`) in a fresh session; the cross-turn case keeps one session.
Assertions read the environment: the synthetic Notes store and the scripts its bridge received, the turn's route, and
the per-demand ledger the runtime publishes on the turn's source context (`TURN_DEMAND_LEDGER_KEY`). LABELLED: model
stand-in, synthetic Notes runner (`FakeNotes`, never osascript), loopback CalDAV fixture from `served_env`.
"""
from __future__ import annotations

import contextlib
import copy
import json
import uuid
from unittest import mock

import pytest

from core.turn_contract import TURN_DEMAND_LEDGER_KEY
from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env  # noqa: F401 -- fixture
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

pytestmark = [pytest.mark.pa_beta]

LANE_OPERATOR = "operator_action_dispatch"
_MIXED_ROUTE = "deterministic:demand_owned_mixed_turn"
_STAND_IN = "MODEL STAND-IN: the general part of this turn was answered here."
_MUTATING = ("make new paragraph", "set name of theNote", "delete theNote")
_READ_SCRIPT = "return body of theNote"

PERSONAL_PLAN = "x-coredata://Note/personal-plan"
ERRANDS_GROCERIES = "x-coredata://Note/errands-groceries"
RECIPES_SOUP = "x-coredata://Note/recipes-soup"
WORK_SOUP = "x-coredata://Note/work-soup"
# "Soup" is in two folders: a folder dropped from the read refuses as ambiguous or shows the other note's body.
_SEED = {
    PERSONAL_PLAN: {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>home chores</div>"},
    ERRANDS_GROCERIES: {"title": "Groceries", "folder": "Errands", "account": "iCloud", "body": "<div>eggs</div>"},
    RECIPES_SOUP: {"title": "Soup", "folder": "Recipes", "account": "iCloud", "body": "<div>lentils</div>"},
    WORK_SOUP: {"title": "Soup", "folder": "Work", "account": "iCloud", "body": "<div>vendor soup list</div>"},
}


@pytest.fixture
def notes_turns(request, monkeypatch):
    workspace = request.getfixturevalue("served_env")["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)
    notes = FakeNotes(copy.deepcopy(_SEED)).install(monkeypatch)
    return workspace, notes


@contextlib.contextmanager
def _session(workspace):
    """Real served turns in one fresh session, the model lane answered by the stand-in. Each turn returns the result
    and the demand ledger the runtime wrote on that turn's own source context."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="operator-binder-fragments", provider_id="operator-binder-fragments",
        provider_name="stand-in", model_name="operator-binder-fragments", output_text=_STAND_IN,
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness(f"sess-operator-binder-{uuid.uuid4().hex[:8]}")

    def turn(text):
        context = dict(_SOURCE_CONTEXT, workspace=str(workspace), operating_mode="auto")
        result = harness.agent.run_once(text, source_context=context, session_id_override=harness.session_id)
        return result, [dict(row) for row in (context.get(TURN_DEMAND_LEDGER_KEY) or [])]

    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            yield turn
    finally:
        harness.close()


def _answer(result):
    return str(result.get("response") or "")


def _assert_nothing_written(notes, case):
    mutating = [script for script in notes.scripts if any(marker in script for marker in _MUTATING)]
    assert mutating == [], f"{case}: a mutating script reached Notes: {mutating}"
    assert notes.notes == _SEED, f"{case}: the Notes store changed"


def _assert_shows_only(result, body, case):
    answer = _answer(result)
    assert body in answer, f"{case}: the named note's body is not shown: {answer[:400]!r}"
    others = [note["body"] for note in _SEED.values() if note["body"] != body and note["body"] in answer]
    assert others == [], f"{case}: another note's body was shown: {others}"


def _assert_dispatched_apart(result, ledger, request_text, fragment, case):
    """THE MIXED-TURN SEAM: the operator lane did not end the turn alone, and each demand ran as its own unit -- the
    request through the operator lane, the fragment through another lane, whose answer the reply carries.

    The operator row's terminal state is not asserted here: a finished Apple Notes read inside a mixed turn is filed
    `pending_approval` at 1d58a641 too (see `test_a_finished_notes_read_in_a_mixed_turn_is_filed_as_executed`)."""
    route = str(result.get("route") or "")
    seen = f"{case}: route={route!r} ledger={json.dumps(ledger)} answer={_answer(result)!r}"
    assert route == _MIXED_ROUTE, seen
    rows = {str(row.get("request") or "").strip(): row for row in ledger}
    assert set(rows) == {request_text, fragment}, seen
    assert rows[request_text]["lane_id"] == LANE_OPERATOR and rows[request_text]["attempted"] is True, seen
    assert rows[fragment]["attempted"] is True and rows[fragment]["terminal_state"] == "executed", seen
    assert rows[fragment]["lane_id"] != LANE_OPERATOR, seen
    assert list(rows[fragment]["member_unit_ids"]) == [rows[fragment]["demand_id"]], seen
    assert _STAND_IN in _answer(result), seen


#: (case, wording, the operator request, the fragment it does not read, the body the read shows)
_MIXED = [
    ("original-trailing-topic", 'open my Apple note "Plan", Ukraine situation', 'open my Apple note "Plan"', "Ukraine situation",
     "<div>home chores</div>"),
    ("original-leading-topic", 'Ukraine situation, open my Apple note "Plan"', 'open my Apple note "Plan"', "Ukraine situation",
     "<div>home chores</div>"),
    ("original-leading-clause", 'draft a reply to that, open my Apple note "Plan"', 'open my Apple note "Plan"',
     "draft a reply to that", "<div>home chores</div>"),
    ("paraphrase-trailing-results", 'show my Apple note "Groceries", the election results', 'show my Apple note "Groceries"',
     "the election results", "<div>eggs</div>"),
    ("sloppy-typos-leading-topic", 'gaza ceasfire talks, read teh Apple note "Groceries"', 'read teh Apple note "Groceries"',
     "gaza ceasfire talks", "<div>eggs</div>"),
    ("scope-rides-topic-trails", 'Recipes folder, show my Apple note "Soup", Ukraine situation',
     'Recipes folder, show my Apple note "Soup"', "Ukraine situation", "<div>lentils</div>"),
]


@pytest.mark.parametrize(("case", "wording", "request_text", "fragment", "body"), _MIXED, ids=[row[0] for row in _MIXED])
def test_a_fragment_the_notes_read_does_not_use_is_dispatched_as_its_own_unit(notes_turns, case, wording, request_text, fragment, body):
    workspace, notes = notes_turns
    with _session(workspace) as turn:
        result, ledger = turn(wording)
    _assert_dispatched_apart(result, ledger, request_text, fragment, case)
    _assert_shows_only(result, body, case)
    assert sum(_READ_SCRIPT in script for script in notes.scripts) == 1, (case, notes.scripts)
    _assert_nothing_written(notes, case)


@pytest.mark.parametrize(("case", "wording", "body"), [
    ("control-novel-folder", 'Recipes folder, show my Apple note "Soup"', "<div>lentils</div>"),
    ("control-other-folder-sloppy", 'Work folder , open my apple note "Soup"', "<div>vendor soup list</div>"),
], ids=["control-novel-folder", "control-other-folder-sloppy"])
def test_a_folder_the_read_uses_still_rides_it(notes_turns, case, wording, body):
    workspace, notes = notes_turns
    with _session(workspace) as turn:
        result, _ledger = turn(wording)
    route = str(result.get("route") or "")
    assert route.startswith("action:operator_action"), f"{case}: never reached the Notes owner alone: route={route!r}"
    _assert_shows_only(result, body, case)
    _assert_nothing_written(notes, case)


def test_a_finished_notes_read_in_a_mixed_turn_is_filed_as_executed(notes_turns):
    # Moved out of xfail on 2026-09-15: `turn_dispatch` now reads the operator's declared effect
    # semantics (`side_effect_class_for_intent`, the tool contracts) instead of the hand list that
    # predated the Apple Notes read kinds, so an executed read is filed as executed.
    workspace, notes = notes_turns
    with _session(workspace) as turn:
        result, ledger = turn('show my Apple note "Groceries", and explain entropy in one line')
    rows = {str(row.get("request") or "").strip(): row for row in ledger}
    assert "<div>eggs</div>" in _answer(result) and sum(_READ_SCRIPT in script for script in notes.scripts) == 1
    assert rows['show my Apple note "Groceries"']["terminal_state"] == "executed", json.dumps(ledger)
    assert "needs your approval" not in _answer(result), _answer(result)


def test_follow_ups_in_one_session_keep_arguments_and_dispatch_topics_apart(notes_turns):
    workspace, notes = notes_turns
    with _session(workspace) as turn:
        first, _ledger = turn('Recipes folder, show my Apple note "Soup"')
        assert str(first.get("route") or "").startswith("action:operator_action"), first.get("route")
        _assert_shows_only(first, "<div>lentils</div>", "cross-turn-first")

        second, ledger = turn('Ukraine situation, open my Apple note "Groceries"')
        _assert_dispatched_apart(second, ledger, 'open my Apple note "Groceries"', "Ukraine situation", "cross-turn-mixed")
        _assert_shows_only(second, "<div>eggs</div>", "cross-turn-mixed")

        third, _ledger = turn('and in the Work folder, show me the Apple note "Soup" too')
        assert str(third.get("route") or "").startswith("action:operator_action"), third.get("route")
        _assert_shows_only(third, "<div>vendor soup list</div>", "cross-turn-follow-up")
    _assert_nothing_written(notes, "cross-turn")

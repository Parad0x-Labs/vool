"""Three routing residuals of the served Notes folder-routing repair, proven through served turns.

Measured 2026-09-15 on the served path (`VoolAgent.run_once`, model stand-in, synthetic Notes runner) at d6a398af:

1. 'append to my Apple note "Ideas" with "count how many python files are in this project"': the workspace-runtime
   search arm planned `workspace.search_text("Ideas")` and answered "No text matches for "Ideas" were found in the
   workspace." No Notes script ran.
2. 'rename my Apple note "Plan" in the Work folder to "Plan v2"': the near-miss intent arbiter was asked before
   operator dispatch. With its model stood in by a `find_folder` pick, `machine.find_folder("Work")` ended the turn.
3. 'write a haiku about rain in this project': the live-info lane answered with its lookup reply. 'write a poem about
   the weather in this folder' reached the model only after a live-data weather plan had run.

Every case is a real served turn in a fresh session. Assertions read the environment: the synthetic Notes store, the
scripts the bridge received, the session event ledger (tool and live-data plan events), the route, the arbiter and
machine-tool recorders and the session's `routing_decisions.jsonl` rows.
LABELLED stand-ins: the answering model; the arbiter's MODEL, replaced by a recorded pick (the gate that decides
whether to ask it, and the pick's execution, are real); the machine find/list/disk implementations, replaced by
recorders so no disk search runs; the synthetic Notes runner (`FakeNotes`, never osascript); the loopback CalDAV
fixture from `served_env`.
"""
from __future__ import annotations

import copy
import json
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env  # noqa: F401 -- fixture
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

pytestmark = [pytest.mark.pa_beta]

WORK_PLAN = "x-coredata://Note/work-plan"
HOME_PLAN = "x-coredata://Note/home-plan"
CURRENT_IDEAS = "x-coredata://Note/current-ideas"
ERRANDS_GROCERIES = "x-coredata://Note/errands-groceries"
_SEED = {
    WORK_PLAN: {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "<div>budget draft</div>"},
    HOME_PLAN: {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>home</div>"},
    CURRENT_IDEAS: {"title": "Ideas", "folder": "Current", "account": "iCloud", "body": "<div>i</div>"},
    ERRANDS_GROCERIES: {"title": "Groceries", "folder": "Errands", "account": "iCloud", "body": "<div>eggs</div>"},
}
_WORKSPACE_FILE = "tide_table.py"
_MODEL_STAND_IN = "MODEL STAND-IN: the model lane answered this turn."
_OPERATOR_OWNER = "declined:registered_owner:operator_action_dispatch"


class _ArbiterModelStandIn:
    """The arbiter's model, replaced by a recorded pick: every consultation is recorded, `pick` is the answer."""

    def __init__(self):
        self.calls: list[dict] = []
        self.pick = ("find_folder", "Work")

    def __call__(self, text, claims, **_kwargs):
        from core.intent_arbiter import ArbiterDecision

        self.calls.append({"text": text, "claims": [claim.family for claim in claims]})
        return ArbiterDecision(*self.pick)


@pytest.fixture
def served(request, monkeypatch):
    import core.runtime_execution_tools as runtime_tools
    from core import intent_arbiter

    workspace = request.getfixturevalue("served_env")["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / _WORKSPACE_FILE).write_text("def tide():\n    return 'TODO: check tide'\n", encoding="utf-8")
    notes = FakeNotes(copy.deepcopy(_SEED)).install(monkeypatch)
    arbiter = _ArbiterModelStandIn()
    monkeypatch.setattr(intent_arbiter, "arbitrate", arbiter)
    machine: list[tuple[str, dict]] = []

    def recorder(intent):
        def run(arguments, *_args, **_kwargs):
            machine.append((intent, dict(arguments or {})))
            return runtime_tools.RuntimeExecutionResult(
                handled=True, ok=True, status="executed", response_text=f"MACHINE STAND-IN {intent}", details={}
            )

        return run

    for name, intent in (
        ("_machine_find_folder", "machine.find_folder"),
        ("_list_machine_directory", "machine.list_directory"),
        ("_machine_disk_usage", "machine.disk_usage"),
    ):
        monkeypatch.setattr(runtime_tools, name, recorder(intent))
    return SimpleNamespace(workspace=workspace, notes=notes, arbiter=arbiter, machine=machine)


def _served(text, workspace):
    """One real served turn in a fresh session; returns the route, the answer, the ledger events and decision rows."""
    from core.memory_first_router import ModelExecutionDecision
    from core.routing_decision_log import decisions_path
    from core.turn_trace import collect_turn_trace

    decision = ModelExecutionDecision(
        source="model", task_hash="served-precedence", provider_id="served-precedence",
        provider_name="stand-in", model_name="served-precedence", output_text=_MODEL_STAND_IN,
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness(f"sess-precedence-{uuid.uuid4().hex[:8]}")
    context = dict(_SOURCE_CONTEXT, workspace=str(workspace), operating_mode="auto")
    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            result = harness.agent.run_once(text, source_context=context, session_id_override=harness.session_id)
        events = list(collect_turn_trace(harness.session_id).get("events") or [])
    finally:
        harness.close()
    rows = []
    path = decisions_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("session_id") == harness.session_id:
                rows.append(row)
    return SimpleNamespace(
        route=str(result.get("route") or ""), answer=str(result.get("response") or ""), events=events, decisions=rows
    )


def _tools_selected(turn):
    return [str(event.get("tool_name") or "") for event in turn.events if event.get("event_type") == "tool_selected"]


def _event_types(turn):
    return [str(event.get("event_type") or "") for event in turn.events]


def _assert_reached_notes(turn, workspace):
    assert turn.route.startswith("action:operator_action"), (
        f"the Notes request never reached operator dispatch: route={turn.route!r} answer={turn.answer[:160]!r}"
    )
    assert str(workspace) not in turn.answer and _WORKSPACE_FILE not in turn.answer, turn.answer[:200]


def _changed(notes):
    edited = {note_id for note_id, note in notes.notes.items() if note != _SEED.get(note_id)}
    return edited | (set(_SEED) - set(notes.notes))


# ---------------------------------------------------------- 1. the workspace search arm and the operator owner

_SEARCH_PAYLOAD_FAMILY = [
    ("original", 'append to my Apple note "Ideas" with "count how many python files are in this project"',
     CURRENT_IDEAS, "body", "<div>i</div>\ncount how many python files are in this project"),
    ("paraphrase-search-command", 'append to my Apple note "Ideas" with "search the codebase for TODO"',
     CURRENT_IDEAS, "body", "<div>i</div>\nsearch the codebase for TODO"),
    ("paraphrase-code-search", 'append to my Apple note "Ideas" with "where is main defined"',
     CURRENT_IDEAS, "body", "<div>i</div>\nwhere is main defined"),
    ("paraphrase-which-files", 'append to my Apple note "Ideas" with "which files mention tide"',
     CURRENT_IDEAS, "body", "<div>i</div>\nwhich files mention tide"),
    ("paraphrase-called", 'append to the apple note called "Ideas" with "look through the workspace and find tide"',
     CURRENT_IDEAS, "body", "<div>i</div>\nlook through the workspace and find tide"),
    ("paraphrase-unrelated-note", 'append to my Apple note "Groceries" with "where is the harbor manifest used in this repo"',
     ERRANDS_GROCERIES, "body", "<div>eggs</div>\nwhere is the harbor manifest used in this repo"),
    ("paraphrase-rename", 'rename my Apple note "Ideas" to "grep for TODO"', CURRENT_IDEAS, "title", "grep for TODO"),
    ("sloppy-filler", 'pls append to my apple note "Ideas" with "count how many python files are in this project" thx',
     CURRENT_IDEAS, "body", "<div>i</div>\ncount how many python files are in this project"),
    ("sloppy-shouted", 'APPEND TO MY APPLE NOTE "Ideas" WITH "SEARCH THE CODEBASE FOR TODO"',
     CURRENT_IDEAS, "body", "<div>i</div>\nSEARCH THE CODEBASE FOR TODO"),
    ("sloppy-bare", 'append to my apple note "Ideas" with "grep for TODO"',
     CURRENT_IDEAS, "body", "<div>i</div>\ngrep for TODO"),
    ("sloppy-question", 'can u append to my apple note "Ideas" with "where is tide defined"?',
     CURRENT_IDEAS, "body", "<div>i</div>\nwhere is tide defined"),
    ("sloppy-number-for-preposition", 'append 2 my apple note "Ideas" with "where is tide defined" pls',
     CURRENT_IDEAS, "body", "<div>i</div>\nwhere is tide defined"),
]


@pytest.mark.parametrize(
    ("case", "text", "note_id", "field", "expected"), _SEARCH_PAYLOAD_FAMILY, ids=[row[0] for row in _SEARCH_PAYLOAD_FAMILY]
)
def test_a_notes_request_carrying_a_search_payload_reaches_notes_and_never_searches(served, case, text, note_id, field,
                                                                                    expected):
    turn = _served(text, served.workspace)
    _assert_reached_notes(turn, served.workspace)
    assert served.notes.notes[note_id][field] == expected, (case, served.notes.notes[note_id])
    assert _changed(served.notes) == {note_id}, f"{case}: only the named note changes"
    assert "workspace.search_text" not in _tools_selected(turn), f"{case}: no workspace search ran"


_WORKSPACE_SEARCH_CONTROLS = [
    ("control-search-codebase", "search the codebase for TODO"),
    ("control-where-defined", "where is tide defined"),
    ("control-look-through", 'look through the workspace and find "tide"'),
    ("control-which-files", "which files mention tide"),
    ("control-grep", "grep for TODO"),
]


@pytest.mark.parametrize(("case", "text"), _WORKSPACE_SEARCH_CONTROLS, ids=[row[0] for row in _WORKSPACE_SEARCH_CONTROLS])
def test_workspace_searches_keep_the_search_arm(served, case, text):
    turn = _served(text, served.workspace)
    assert turn.route == "deterministic:workspace_runtime_fast_path", (case, turn.route, turn.answer[:160])
    assert "workspace.search_text" in _tools_selected(turn)
    assert _WORKSPACE_FILE in turn.answer, f"{case}: the real search found the workspace file"
    assert served.notes.scripts == [], f"{case}: never reaches the Notes bridge"


def test_a_notes_search_is_not_a_workspace_search(served):
    """Near-miss: reads like a search, belongs to the Notes lane."""
    turn = _served('search my Apple notes for "TODO"', served.workspace)
    assert turn.route.startswith("action:operator_action"), (turn.route, turn.answer[:160])
    assert "workspace.search_text" not in _tools_selected(turn)


# ------------------------------------------------------------------ 2. the intent arbiter and the operator owner

_ARBITER_FAMILY = [
    ("original", 'rename my Apple note "Plan" in the Work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-append", 'append to my Apple note "Plan" in the Work folder with "reviewed budget"',
     WORK_PLAN, "body", "<div>budget draft</div>\nreviewed budget"),
    ("paraphrase-scope-first", 'in the Work folder, rename my Apple note "Plan" to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-current-folder", 'rename my Apple note "Ideas" in the Current folder to "Ideas 2"',
     CURRENT_IDEAS, "title", "Ideas 2"),
    ("paraphrase-folder-payload", 'append to my Apple note "Ideas" with "the folder structure looks fine"',
     CURRENT_IDEAS, "body", "<div>i</div>\nthe folder structure looks fine"),
    ("paraphrase-file-payload", 'append to my Apple note "Ideas" with "check the file sizes later"',
     CURRENT_IDEAS, "body", "<div>i</div>\ncheck the file sizes later"),
    ("paraphrase-disk-title", 'rename my Apple note "Ideas" to "disk cleanup plan"', CURRENT_IDEAS, "title", "disk cleanup plan"),
    ("sloppy-filler", 'pls rename my apple note "Plan" in the work folder to "Plan v2" thx', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-shouted", 'RENAME MY APPLE NOTE "Plan" IN THE WORK FOLDER TO "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-ram-cpu", 'append to my apple note "Ideas" with "ram and cpu notes"',
     CURRENT_IDEAS, "body", "<div>i</div>\nram and cpu notes"),
    ("sloppy-question", 'can u append to my apple note "Plan" in the work folder with "reviewed budget"?',
     WORK_PLAN, "body", "<div>budget draft</div>\nreviewed budget"),
    ("sloppy-unrelated-note", 'append to my apple note "Groceries" with "files for the storage unit"',
     ERRANDS_GROCERIES, "body", "<div>eggs</div>\nfiles for the storage unit"),
]


@pytest.mark.parametrize(
    ("case", "text", "note_id", "field", "expected"), _ARBITER_FAMILY, ids=[row[0] for row in _ARBITER_FAMILY]
)
def test_a_notes_near_miss_never_consults_the_arbiter(served, case, text, note_id, field, expected):
    turn = _served(text, served.workspace)
    _assert_reached_notes(turn, served.workspace)
    assert served.notes.notes[note_id][field] == expected, (case, served.notes.notes[note_id])
    assert _changed(served.notes) == {note_id}, f"{case}: only the named note changes"
    assert served.arbiter.calls == [], f"{case}: the arbiter was asked about a turn the operator lane owns"
    assert served.machine == [], f"{case}: no machine read ran"
    assert _OPERATOR_OWNER in [row.get("arbiter") for row in turn.decisions], turn.decisions


def test_a_folder_scoped_delete_asks_first_without_the_arbiter(served):
    turn = _served('delete my Apple note "Plan" in the Work folder', served.workspace)
    assert turn.route == "action:operator_action_approval_required", turn.route
    assert _changed(served.notes) == set()
    assert served.arbiter.calls == [] and served.machine == []


def test_a_typo_near_miss_with_no_registered_owner_still_reaches_the_arbiter(served):
    served.arbiter.pick = ("find_folder", "oken hunter")
    turn = _served("fint the oken hunter folder", served.workspace)
    assert len(served.arbiter.calls) == 1
    assert turn.route == "deterministic:intent_arbiter_fast_path", turn.route
    assert served.machine == [("machine.find_folder", {"name": "oken hunter"})]


def test_an_ambiguous_machine_question_keeps_the_arbiters_pick(served):
    """Owned by the machine-fact lane, a domain the menu reads: a sibling reading, so the arbiter still decides."""
    served.arbiter.pick = ("disk_usage", "")
    turn = _served("How much disk space is left on this machine?", served.workspace)
    assert len(served.arbiter.calls) == 1, served.arbiter.calls
    assert served.machine == [("machine.disk_usage", {})]
    assert turn.route == "deterministic:intent_arbiter_fast_path", turn.route


def test_a_workspace_folder_question_keeps_the_workspace_lane(served):
    turn = _served("what's in the work folder?", served.workspace)
    assert turn.route == "deterministic:folder_overview_fast_path", turn.route
    assert _WORKSPACE_FILE in turn.answer
    assert served.notes.scripts == [] and served.arbiter.calls == []


# ------------------------------------------------------------ 3. a poem whose subject is a weather word

_CREATIVE_FAMILY = [
    ("original", "write a haiku about rain in this project"),
    ("paraphrase-compose", "compose a short poem about rain for this project"),
    ("paraphrase-give-me", "give me a haiku on the rain in this repo"),
    ("paraphrase-limerick", "could you write a limerick about snow in this workspace"),
    ("paraphrase-sonnet", "i'd like a sonnet about the wind in this folder"),
    ("paraphrase-verse", "draft a few lines of verse about rain in this project"),
    ("paraphrase-city", "write a haiku about rain in Vilnius"),
    ("paraphrase-weather-folder", "write a poem about the weather in this folder"),
    ("sloppy-typos", "write a haiku abt rain in this projct"),
    ("sloppy-shouted", "WRITE A HAIKU ABOUT RAIN IN THIS PROJECT"),
    ("sloppy-filler", "pls write me a haiku bout rain in this project thx"),
    ("sloppy-no-article", "can u write haiku about rain in this project"),
    ("sloppy-casual", "yo compose a quick haiku about the rain in this repo"),
]


@pytest.mark.parametrize(("case", "text"), _CREATIVE_FAMILY, ids=[row[0] for row in _CREATIVE_FAMILY])
def test_a_poem_about_a_weather_word_is_written_by_the_model_without_a_lookup(served, case, text):
    turn = _served(text, served.workspace)
    assert turn.answer == _MODEL_STAND_IN, (case, turn.route, turn.answer[:160])
    assert "live_data_plan_created" not in _event_types(turn), f"{case}: a live-data plan ran"
    assert "live_info_fast_path" not in [row.get("family") for row in turn.decisions], f"{case}: the live-info lane claimed"
    assert served.notes.scripts == []


_LIVE_CONTROLS = [
    ("control-will-it-rain", "will it rain in Vilnius today?"),
    ("control-present-state-poem", "write a haiku about today's weather in Vilnius"),
    ("control-write-down-whether", "write down whether it will rain in Vilnius today"),
    ("control-fragment", "rain in london tomorrow?"),
]


@pytest.mark.parametrize(("case", "text"), _LIVE_CONTROLS, ids=[row[0] for row in _LIVE_CONTROLS])
def test_a_request_about_the_present_weather_keeps_the_live_lane(served, case, text):
    turn = _served(text, served.workspace)
    assert turn.route == "deterministic:live_info_fast_path", (case, turn.route, turn.answer[:160])


def test_a_poem_with_no_weather_word_is_unchanged(served):
    turn = _served("write a haiku about rain", served.workspace)
    assert turn.answer == _MODEL_STAND_IN
    assert "live_data_plan_created" not in _event_types(turn)


def test_a_project_question_keeps_the_overview(served):
    turn = _served("what's in this project?", served.workspace)
    assert turn.route == "deterministic:folder_overview_fast_path", turn.route


# ------------------------------------------------------------------------ recorded, still open (strict xfail)

_OPEN_CREATIVE = [
    pytest.param(
        "haiku about rain in this project pls",
        marks=pytest.mark.xfail(strict=True, reason=(
            "open: no writing verb or want lead-in, so the creative authority "
            "(core.creative_director.detect_prose_request) does not read a request for writing"
        )),
        id="open-verbless-fragment",
    ),
    pytest.param(
        "write a haiku about rain in this codebase",
        marks=pytest.mark.xfail(strict=True, reason=(
            "open: the creative authority reads 'codebase' as a programming context and declines the prose reading"
        )),
        id="open-codebase-context",
    ),
]


@pytest.mark.parametrize("text", _OPEN_CREATIVE)
def test_recorded_open_creative_variants(served, text):
    turn = _served(text, served.workspace)
    assert turn.answer == _MODEL_STAND_IN and "live_data_plan_created" not in _event_types(turn)


@pytest.mark.xfail(strict=True, reason=(
    "open: the live-info door reads the quoted payload ('find all references to tide in this project') as a fresh "
    "lookup. Registry ownership is the wrong authority at that door: across the test corpus a yield to the registered "
    "whole-turn owner would move 49 sentences, 48 of them live lookups (FX rates owned by the currency lanes, which run "
    "earlier in the front door; 'What is the latest stable version of Node.js?' owned by the read lane, which declines "
    "it; web release-note lookups the operator parser also reads)"
))
def test_recorded_open_notes_append_whose_payload_reads_as_a_lookup(served):
    turn = _served('please append to my Apple note "Ideas" the line "find all references to tide in this project"',
                   served.workspace)
    _assert_reached_notes(turn, served.workspace)


@pytest.mark.xfail(strict=True, reason=(
    "open: the machine write guard (fast_paths_machine.looks_like_safe_machine_write_request) reads 'append' and the "
    "payload's 'downloads' as a machine write. Registry ownership is the wrong authority here: across the test corpus "
    "the same yield would move 4 sentences, all to false operator_action_dispatch claims ('write a note to my desktop "
    "called hi.txt')"
))
def test_recorded_open_notes_append_naming_a_machine_folder(served):
    turn = _served('append to my Apple note "Ideas" with "the files in my downloads folder"', served.workspace)
    _assert_reached_notes(turn, served.workspace)

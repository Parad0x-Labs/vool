"""A Notes request that names a Notes folder reaches Notes through the served turn; a workspace-folder question keeps the workspace.

Measured 2026-09-15 on the served path (`VoolAgent.run_once`, model stand-in, synthetic Notes runner), before this repair:
'rename my Apple note "Plan" in the Work folder to "Plan v2"' was answered "The workspace folder — `<workspace>` — is empty",
and no Notes script ran. The same text parses at the operator boundary as `apple_note_rename`. The folder-overview
admission is an arm of `turn_frontdoor_deterministic` (catalog precedence 50). It runs in the front door before operator
dispatch (`operator_action_dispatch`, precedence 48), and its loose arm read "the Work folder" as the bound workspace.
The identity admission answered 'append to my Apple note "Plan" with "which folder am I in?"' with the bound folder's
name in the same way. Both admissions now ask the registry first
(`core.agent_runtime.demand_ownership.registered_owner_ahead_of`).

Every case is a real served turn in a fresh session. Assertions read the environment: the synthetic Notes store, the
scripts the bridge received, the route and the real workspace listing. LABELLED: model stand-in, synthetic Notes runner
(`FakeNotes`, never osascript), loopback CalDAV fixture from `served_env`.
"""
from __future__ import annotations

import copy
import uuid
from unittest import mock

import pytest

from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env  # noqa: F401 -- fixture
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

pytestmark = [pytest.mark.pa_beta]

WORK_PLAN = "x-coredata://Note/work-plan"
HOME_PLAN = "x-coredata://Note/home-plan"
LOCAL_PACK = "x-coredata://Note/local-pack"
TRAVEL_PACK = "x-coredata://Note/travel-pack"
CURRENT_IDEAS = "x-coredata://Note/current-ideas"
ERRANDS_GROCERIES = "x-coredata://Note/errands-groceries"

# Two same-titled pairs, so a folder scope that is dropped or misread lands on the wrong note and shows.
_SEED = {
    WORK_PLAN: {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "<div>budget draft</div>"},
    HOME_PLAN: {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>home</div>"},
    LOCAL_PACK: {"title": "Packing list", "folder": "Local", "account": "iCloud", "body": "<div>socks</div>"},
    TRAVEL_PACK: {"title": "Packing list", "folder": "Travel", "account": "iCloud", "body": "<div>maps</div>"},
    CURRENT_IDEAS: {"title": "Ideas", "folder": "Current", "account": "iCloud", "body": "<div>i</div>"},
    ERRANDS_GROCERIES: {"title": "Groceries", "folder": "Errands", "account": "iCloud", "body": "<div>eggs</div>"},
}
_WORKSPACE_FILE = "harbor_manifest.txt"
_MUTATING = ("make new paragraph", "set name of theNote", "delete theNote")


@pytest.fixture
def scoped(request, monkeypatch):
    workspace = request.getfixturevalue("served_env")["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / _WORKSPACE_FILE).write_text("crates: 12\n", encoding="utf-8")
    (workspace / "tide_table.py").write_text("print('tide')\n", encoding="utf-8")
    notes = FakeNotes(copy.deepcopy(_SEED)).install(monkeypatch)
    return workspace, notes


def _served(text, workspace):
    """One real served turn in a fresh session, the model lane answered by the stand-in `_turn` uses.

    `_turn` returns only the reply text; these assertions also need the route."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="served-notes-scope", provider_id="served-notes-scope",
        provider_name="stand-in", model_name="served-notes-scope",
        output_text="MODEL STAND-IN: no general answer needed for this operator turn.",
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness(f"sess-notes-scope-{uuid.uuid4().hex[:8]}")
    context = dict(_SOURCE_CONTEXT, workspace=str(workspace), operating_mode="auto")
    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            return harness.agent.run_once(text, source_context=context, session_id_override=harness.session_id)
    finally:
        harness.close()


def _assert_reached_notes(result, workspace):
    route = str(result.get("route") or "")
    answer = str(result.get("response") or "")
    assert route.startswith("action:operator_action"), (
        f"the Notes request never reached operator dispatch: route={route!r} answer={answer[:160]!r}"
    )
    assert str(workspace) not in answer and _WORKSPACE_FILE not in answer, (
        f"the Notes request was answered from the workspace: {answer[:200]!r}"
    )


def _changed(notes):
    edited = {note_id for note_id, note in notes.notes.items() if note != _SEED.get(note_id)}
    return edited | (set(_SEED) - set(notes.notes))


def _mutating_scripts(notes):
    return [script for script in notes.scripts if any(marker in script for marker in _MUTATING)]


# ----------------------------------------------------------------------------- the three required proofs


def test_original_folder_scoped_rename_renames_only_the_note_in_that_folder(scoped):
    workspace, notes = scoped
    result = _served('rename my Apple note "Plan" in the Work folder to "Plan v2"', workspace)
    _assert_reached_notes(result, workspace)
    assert notes.notes[WORK_PLAN]["title"] == "Plan v2"
    assert _changed(notes) == {WORK_PLAN}, "the same-titled note in Personal is untouched"
    assert [script for script in _mutating_scripts(notes) if 'set name of theNote to "Plan v2"' in script]


def test_novel_folder_scoped_append_writes_only_the_note_in_that_folder(scoped):
    workspace, notes = scoped
    result = _served('append to my Apple note "Packing list" in the Local folder with "passport and charger"', workspace)
    _assert_reached_notes(result, workspace)
    assert notes.notes[LOCAL_PACK]["body"] == "<div>socks</div>\npassport and charger"
    assert _changed(notes) == {LOCAL_PACK}, "the same-titled note in Travel is untouched"


def test_a_workspace_folder_question_is_still_answered_by_the_workspace_lane(scoped):
    workspace, notes = scoped
    result = _served("what's in the work folder?", workspace)
    assert result.get("route") == "deterministic:folder_overview_fast_path", result.get("route")
    assert _WORKSPACE_FILE in str(result.get("response") or ""), "the listing names the real workspace file"
    assert notes.scripts == [], "a workspace question never reaches the Notes bridge"


# ------------------------------------------------------------------------------------------ the family

# (case, text, the note that must change, field, expected value). Every other note must stay as seeded.
_EFFECT_FAMILY = [
    ("paraphrase-new-title-first", 'rename my Apple note "Plan" to "Plan v2" in the Work folder', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-scope-first", 'in the Work folder, rename my Apple note "Plan" to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-append", 'append to my Apple note "Plan" in the Work folder with "reviewed budget"',
     WORK_PLAN, "body", "<div>budget draft</div>\nreviewed budget"),
    ("paraphrase-current-folder", 'rename my Apple note "Ideas" in the Current folder to "Ideas 2"', CURRENT_IDEAS, "title", "Ideas 2"),
    ("sloppy-filler", 'pls rename my apple note "Plan" in the work folder to "Plan v2" thx', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-shouted", 'RENAME MY APPLE NOTE "Plan" IN THE WORK FOLDER TO "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-question", 'can u append to my apple note "Plan" in the work folder with "reviewed budget"?',
     WORK_PLAN, "body", "<div>budget draft</div>\nreviewed budget"),
    ("sloppy-bangs", 'rename my Apple note "Plan" in the Work folder to "Plan v2"!!', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-spacing-asap", 'append to  my Apple note "Packing list"  in the local folder with "passport and charger" asap',
     LOCAL_PACK, "body", "<div>socks</div>\npassport and charger"),
    # adversarial near-misses: the payload itself reads as a workspace question
    ("near-miss-identity-payload", 'append to my Apple note "Ideas" with "which folder am I in?"',
     CURRENT_IDEAS, "body", "<div>i</div>\nwhich folder am I in?"),
    ("near-miss-overview-payload", 'append to my Apple note "Ideas" with "review the project budget"',
     CURRENT_IDEAS, "body", "<div>i</div>\nreview the project budget"),
    ("near-miss-measurement-payload", 'append to my Apple note "Ideas" with "number of python files"',
     CURRENT_IDEAS, "body", "<div>i</div>\nnumber of python files"),
    # unrelated names: a folder name that never collided with the workspace vocabulary keeps working
    ("unrelated-folder", 'append to my Apple note "Groceries" in the Errands folder with "oat milk"',
     ERRANDS_GROCERIES, "body", "<div>eggs</div>\noat milk"),
]


@pytest.mark.parametrize(
    ("case", "text", "note_id", "field", "expected"), _EFFECT_FAMILY, ids=[row[0] for row in _EFFECT_FAMILY]
)
def test_folder_scoped_notes_family_reaches_notes_with_its_effect(scoped, case, text, note_id, field, expected):
    workspace, notes = scoped
    result = _served(text, workspace)
    _assert_reached_notes(result, workspace)
    assert notes.notes[note_id][field] == expected, (case, notes.notes[note_id])
    assert _changed(notes) == {note_id}, f"{case}: only the named note changes"


def test_a_folder_scoped_delete_reaches_notes_and_asks_first(scoped):
    workspace, notes = scoped
    result = _served('delete my Apple note "Plan" in the Work folder', workspace)
    _assert_reached_notes(result, workspace)
    assert result.get("route") == "action:operator_action_approval_required", result.get("route")
    assert _changed(notes) == set() and _mutating_scripts(notes) == [], "nothing is deleted before approval"


# At d6a398af these reached Notes and the Notes owner declined them without writing: "the Work folder of the iCloud
# account" gave the account "Work folder of the iCloud", "teh work folder" gave the folder "teh work", and the read kind
# did not narrow by folder. The scope-reading repair (core.operator.apple_notes._ScopeReader) makes them act; the
# wider family is tests/pa_beta_gate/test_served_notes_scope_reading.py.
_SCOPE_EFFECTS = [
    ("paraphrase-folder-of-account", 'rename my Apple note "Plan" in the Work folder of the iCloud account to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-typo-determiner", 'rename my apple note "Plan" in teh work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
]


@pytest.mark.parametrize(("case", "text", "note_id", "field", "expected"), _SCOPE_EFFECTS, ids=[row[0] for row in _SCOPE_EFFECTS])
def test_folder_scoped_notes_requests_act_on_the_note_in_that_scope(scoped, case, text, note_id, field, expected):
    workspace, notes = scoped
    result = _served(text, workspace)
    _assert_reached_notes(result, workspace)
    assert notes.notes[note_id][field] == expected, (case, notes.notes[note_id])
    assert _changed(notes) == {note_id}, f"{case}: only the named note changes"


def test_a_folder_scoped_show_shows_the_note_in_that_folder(scoped):
    workspace, notes = scoped
    result = _served('show my Apple note "Plan" in the Work folder', workspace)
    _assert_reached_notes(result, workspace)
    answer = str(result.get("response") or "")
    assert "<div>budget draft</div>" in answer and "<div>home</div>" not in answer, answer[:300]
    assert _mutating_scripts(notes) == [] and _changed(notes) == set(), "a read writes nothing"


# ------------------------------------------------------------------------------------ negative controls

_WORKSPACE_CONTROLS = [
    ("control-work-folder", "what's in the work folder?", "deterministic:folder_overview_fast_path"),
    ("control-this-folder-sloppy", "whats in this folder", "deterministic:folder_overview_fast_path"),
    ("control-local-folder-we-are-in", "explain the local folder we are in", "deterministic:folder_overview_fast_path"),
    ("control-which-folder", "which folder am I in?", "deterministic:workspace_identity_fast_path"),
    ("control-notes-folder-of-project", "what's in the notes folder of this project?", "deterministic:folder_overview_fast_path"),
    ("control-count-python-files", "count how many python files are in this project", "deterministic:workspace_measurement_fast_path"),
]


@pytest.mark.parametrize(("case", "text", "route"), _WORKSPACE_CONTROLS, ids=[row[0] for row in _WORKSPACE_CONTROLS])
def test_workspace_questions_keep_the_workspace_lanes(scoped, case, text, route):
    workspace, notes = scoped
    result = _served(text, workspace)
    answer = str(result.get("response") or "")
    assert result.get("route") == route, (case, result.get("route"), answer[:160])
    assert str(workspace) in answer, f"{case}: answered from the bound workspace"
    assert notes.scripts == [], f"{case}: never reaches the Notes bridge"

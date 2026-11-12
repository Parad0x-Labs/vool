"""A served request that names an Apple note reads or creates it in Apple Notes, never in the workspace.

Measured 2026-09-15 at 1d58a641 on the served path (`NullaAgent.run_once`, model stand-in, synthetic Notes runner):
* 'in the On My Mac account, find my Apple note "Plan"' and 'find my Apple note "Plan" in the On My Mac account' were
  answered 'No notes in this workspace match ...' -- a workspace search;
* 'In my Travel folder, create an Apple note titled "Trip" with: pack chargers' and 'create an Apple note titled "Trip"
  with: pack chargers in my Travel folder' wrote a workspace note and ran no Notes create script, while 'save a note to
  Apple Notes in the Travel folder titled "Trip" with: pack chargers' created it in Apple Notes;
* 'find my Apple notes in the Travel folder' was claimed at the front door by the machine read lane, which started a
  machine-wide folder search for "Travel" (the base run stalled inside it for 11 minutes).

Every case is a real served turn in a fresh session (cross-turn cases continue their own session). Assertions read the
environment: the synthetic Notes store and the scripts it received, the create scripts the bridge built, the workspace
notes directory, the machine-wide searches the front door requested, and the route. The parser and destination seam is
pinned in tests/test_apple_notes_object_names_the_store.py.
LABELLED: model stand-in, synthetic Notes runner (`FakeNotes`, never osascript), a runner that records create scripts,
recorders standing in for the machine-wide folder and file searches (they record the request and search nothing), loopback
CalDAV fixture from `served_env`.
"""
from __future__ import annotations

import contextlib
import copy
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env  # noqa: F401 -- fixture
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

pytestmark = [pytest.mark.pa_beta]

_MUTATING = ("make new paragraph", "set name of theNote", "delete theNote", "make new note")
_READS_A_BODY = "return body of theNote"
_LISTS_NOTES = "repeat with theNote in notes"

# Titles held by several notes, so a dropped or misread scope refuses as ambiguous or shows another note's body; a title
# held by one note, so the store alone decides.
MAC_PLAN = "x-coredata://Note/mac-plan"
WORK_PLAN = "x-coredata://Note/work-plan"
HOME_PLAN = "x-coredata://Note/home-plan"
TRAVEL_PACK = "x-coredata://Note/travel-pack"
LOCAL_PACK = "x-coredata://Note/local-pack"
ERRANDS_GROCERIES = "x-coredata://Note/errands-groceries"
GMAIL_TAXES = "x-coredata://Note/gmail-taxes-receipts"
GMAIL_HOME = "x-coredata://Note/gmail-home-receipts"
_SEED = {
    MAC_PLAN: {"title": "Plan", "folder": "Work", "account": "On My Mac", "body": "<div>offline copy</div>"},
    WORK_PLAN: {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "<div>budget draft</div>"},
    HOME_PLAN: {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>home chores</div>"},
    TRAVEL_PACK: {"title": "Packing list", "folder": "Travel", "account": "iCloud", "body": "<div>maps</div>"},
    LOCAL_PACK: {"title": "Packing list", "folder": "Local", "account": "iCloud", "body": "<div>socks</div>"},
    ERRANDS_GROCERIES: {"title": "Groceries", "folder": "Errands", "account": "iCloud", "body": "<div>eggs</div>"},
    GMAIL_TAXES: {"title": "Receipts", "folder": "Taxes 2026", "account": "Gmail", "body": "<div>q1 filed</div>"},
    GMAIL_HOME: {"title": "Receipts", "folder": "Home", "account": "Gmail", "body": "<div>q3 boiler</div>"},
}


@pytest.fixture
def served_notes(request, monkeypatch):
    workspace = request.getfixturevalue("served_env")["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)
    notes = FakeNotes(copy.deepcopy(_SEED)).install(monkeypatch)
    from core import runtime_execution_tools
    from core.operator import apple_notes

    creates: list[str] = []

    def runner(command, **kwargs):
        creates.append(command[-1])
        return SimpleNamespace(returncode=0, stdout=f"note id x-coredata://Note/created-{len(creates)}", stderr="")

    real_create = apple_notes.create_apple_note
    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real_create(**kw, runner=runner))

    disk_searches: list[tuple[str, dict]] = []

    def _recorder(tool):
        def run(arguments, **kwargs):
            disk_searches.append((tool, dict(arguments or {})))
            return runtime_execution_tools.RuntimeExecutionResult(
                handled=True, ok=False, status="no_results",
                response_text=f"TEST RECORDER: {tool} was requested and nothing was searched.")
        return run

    monkeypatch.setattr(runtime_execution_tools, "_machine_find_folder", _recorder("machine.find_folder"))
    monkeypatch.setattr(runtime_execution_tools, "_machine_find_file", _recorder("machine.find_file"))
    return SimpleNamespace(workspace=workspace, notes=notes, creates=creates, disk_searches=disk_searches)


@contextlib.contextmanager
def _session(env):
    """Real served turns in one fresh session, the model lane answered by the stand-in.

    After every turn: no machine-wide folder or file search was requested for it."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="apple-notes-object", provider_id="apple-notes-object",
        provider_name="stand-in", model_name="apple-notes-object",
        output_text="MODEL STAND-IN: no general answer needed for this operator turn.",
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness(f"sess-apple-notes-object-{uuid.uuid4().hex[:8]}")
    context = dict(_SOURCE_CONTEXT, workspace=str(env.workspace), operating_mode="auto")

    def turn(text):
        result = harness.agent.run_once(text, source_context=dict(context), session_id_override=harness.session_id)
        assert env.disk_searches == [], f"{text!r}: the front door requested a machine-wide search: {env.disk_searches}"
        return result

    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            yield turn
    finally:
        harness.close()


def _served(text, env):
    with _session(env) as turn:
        return turn(text)


def _answer(result):
    return str(result.get("response") or "")


def _route(result):
    return str(result.get("route") or "")


def _workspace_notes(workspace):
    folder = workspace / "notes"
    return sorted(path.name for path in folder.glob("*.md")) if folder.is_dir() else []


def _changed(notes):
    return {note_id for note_id, note in notes.notes.items() if note != _SEED.get(note_id)} | (set(_SEED) - set(notes.notes))


def _assert_reached_notes(result, case):
    assert _route(result).startswith("action:operator_action"), (
        f"{case}: never reached the Notes owner: route={_route(result)!r} answer={_answer(result)[:240]!r}")


def _assert_read_only_that_note(env, result, body, case):
    answer = _answer(result)
    assert body in answer, f"{case}: the named note's body is not shown: {answer[:300]!r}"
    others = [note["body"] for note in _SEED.values() if note["body"] != body and note["body"] in answer]
    assert others == [], f"{case}: another note's body was shown: {others}"
    assert "in this workspace" not in answer, f"{case}: answered from the workspace: {answer[:300]!r}"
    assert any(_READS_A_BODY in script for script in env.notes.scripts), f"{case}: no Notes body read ran: {env.notes.scripts}"


def _assert_nothing_written(env, case):
    mutating = [script for script in env.notes.scripts if any(marker in script for marker in _MUTATING)]
    assert mutating == [], f"{case}: a mutating script reached Notes: {mutating}"
    assert _changed(env.notes) == set(), f"{case}: the Notes store changed: {sorted(_changed(env.notes))}"
    assert env.creates == [], f"{case}: a Notes create script ran: {env.creates}"
    assert _workspace_notes(env.workspace) == [], f"{case}: a workspace note was written: {_workspace_notes(env.workspace)}"


# ------------------------------------------------------------------------------------------- one named note, read

# (case, wording, the body shown). The reported wordings first; the verb typo is repaired by the served input normalizer.
_READ_FAMILY = [
    ("original-account-first", 'in the On My Mac account, find my Apple note "Plan"', "<div>offline copy</div>"),
    ("original-account-last", 'find my Apple note "Plan" in the On My Mac account', "<div>offline copy</div>"),
    ("paraphrase-search-for-in-folder", 'search for my Apple note "Plan" in the Personal folder', "<div>home chores</div>"),
    ("paraphrase-look-up-possessive-account", 'look up the Apple note "Receipts" from my Gmail account\'s Home folder',
     "<div>q3 boiler</div>"),
    ("paraphrase-polite-question-titled",
     'can you find the Apple note titled "Receipts" in the Taxes 2026 folder of my Gmail account', "<div>q1 filed</div>"),
    ("paraphrase-scope-first-look-for-called", "in the Personal folder, look for my Apple note called Plan", "<div>home chores</div>"),
    ("paraphrase-store-as-a-place", 'find my note "Groceries" in Apple Notes', "<div>eggs</div>"),
    ("paraphrase-notes-app-as-a-place", 'look for the note "Packing list" from the Travel folder in the Notes app', "<div>maps</div>"),
    ("paraphrase-title-before-the-notes", 'find "Packing list" among my Apple notes in the Local folder', "<div>socks</div>"),
    ("paraphrase-open-in-notes-dot-app", 'open the note "Groceries" in Notes.app', "<div>eggs</div>"),
    ("paraphrase-show-from-the-app", 'show my note "Plan" from Apple Notes, Personal folder', "<div>home chores</div>"),
    ("sloppy-shouted", 'FIND MY APPLE NOTE "Plan" IN THE ON MY MAC ACCOUNT', "<div>offline copy</div>"),
    ("sloppy-store-typo", 'find my aple note "Plan" in the On My Mac account', "<div>offline copy</div>"),
    ("sloppy-no-determiners-lowercase", 'find apple note "Packing list" in travel folder', "<div>maps</div>"),
    ("sloppy-dropped-preposition", 'look up my apple note "Plan" personal folder', "<div>home chores</div>"),
    ("sloppy-casual-filler", 'yo can u find apple note "Receipts" in the Home folder thx', "<div>q3 boiler</div>"),
    ("sloppy-reordered-scope-before-object", 'find in the Personal folder my Apple note "Plan"', "<div>home chores</div>"),
    ("sloppy-verb-typo-normalized", 'serach for my apple note "Packing list" in teh travel fodler', "<div>maps</div>"),
    ("sloppy-transposed-store", 'search for my appel note "Receipts" in the Home folder', "<div>q3 boiler</div>"),
]


@pytest.mark.parametrize(("case", "text", "body"), _READ_FAMILY, ids=[row[0] for row in _READ_FAMILY])
def test_a_request_to_find_an_apple_note_shows_that_note_from_apple_notes(served_notes, case, text, body):
    result = _served(text, served_notes)
    _assert_reached_notes(result, case)
    _assert_read_only_that_note(served_notes, result, body, case)
    _assert_nothing_written(served_notes, case)


# ----------------------------------------------------------------------------------------------- the notes, listed

# (case, wording, note ids listed, the find topic the reply says it did not search for)
_LISTING_FAMILY = [
    ("listing-find-in-folder", "find my Apple notes in the Travel folder", {TRAVEL_PACK}, None),
    ("listing-search-topic", "search my Apple Notes for packing", set(_SEED), "packing"),
    ("listing-store-as-a-place-scoped", "find my notes in Apple Notes in the Gmail account", {GMAIL_TAXES, GMAIL_HOME}, None),
    ("listing-sloppy", "find all my apple notes in teh local folder pls", {LOCAL_PACK}, None),
]


@pytest.mark.parametrize(("case", "text", "listed", "topic"), _LISTING_FAMILY, ids=[row[0] for row in _LISTING_FAMILY])
def test_a_request_to_find_apple_notes_lists_them_from_apple_notes_and_reads_no_body(served_notes, case, text, listed, topic):
    result = _served(text, served_notes)
    _assert_reached_notes(result, case)
    answer = _answer(result)
    assert {note_id for note_id in _SEED if note_id in answer} == listed, (case, answer[:600])
    assert "in this workspace" not in answer, (case, answer[:300])
    assert any(_LISTS_NOTES in script for script in served_notes.notes.scripts), (case, served_notes.notes.scripts)
    assert not any(_READS_A_BODY in script for script in served_notes.notes.scripts), f"{case}: a listing read a note body"
    if topic is None:
        assert "did not search" not in answer, (case, answer[:600])
    else:
        assert f"did not search Apple Notes for {topic!r}" in answer, (case, answer[:600])
    _assert_nothing_written(served_notes, case)


# --------------------------------------------------------------------------------------------------------- create

# (case, wording, the location clause of the create script -- None for the default location, the body written)
_CREATE_FAMILY = [
    ("original-scope-first", 'In my Travel folder, create an Apple note titled "Trip" with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
    ("original-scope-in-the-payload", 'create an Apple note titled "Trip" with: pack chargers in my Travel folder',
     None, "pack chargers in my Travel folder"),
    ("paraphrase-save-scope-before-payload", 'save an Apple note titled "Trip" in my Travel folder with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
    ("paraphrase-store-scope-before-title", 'store an Apple note in the Travel folder titled "Trip" with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
    ("paraphrase-jot-down-account", 'jot down an Apple note titled "Trip" in my iCloud account with: pack chargers',
     'at account "iCloud"', "pack chargers"),
    ("paraphrase-new-note-folder-of-account",
     'create a new Apple note in the Travel folder of my iCloud account titled "Trip" with: pack chargers',
     'at folder "Travel" of account "iCloud"', "pack chargers"),
    ("paraphrase-notes-app-as-a-place", 'save a note titled "Trip" to the Notes app in my Travel folder with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
    ("sloppy-lowercase-no-articles", 'create apple note "Trip" in travel folder with: pack chargers',
     'at folder "travel" of default account', "pack chargers"),
    ("sloppy-shouted", 'CREATE AN APPLE NOTE TITLED "Trip" IN MY TRAVEL FOLDER WITH: pack chargers',
     'at folder "TRAVEL" of default account', "pack chargers"),
    ("sloppy-store-typo", 'create an aple note titled "Trip" in my Travel folder with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
    ("sloppy-dropped-preposition", 'create apple note "Trip" Travel folder with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
    ("sloppy-verb-typo-normalized", 'crate an apple note titled "Trip" in my Travel folder with: pack chargers',
     'at folder "Travel" of default account', "pack chargers"),
]


@pytest.mark.parametrize(("case", "text", "location", "body"), _CREATE_FAMILY, ids=[row[0] for row in _CREATE_FAMILY])
def test_a_request_to_create_an_apple_note_creates_it_in_apple_notes_where_it_names(served_notes, case, text, location, body):
    result = _served(text, served_notes)
    _assert_reached_notes(result, case)
    assert len(served_notes.creates) == 1, (case, served_notes.creates, _answer(result)[:300])
    script = served_notes.creates[0]
    assert f'name:"Trip", body:"{body}"' in script, (case, script)
    if location is None:
        assert "make new note with properties" in script, (case, script)
    else:
        assert f"make new note {location} with properties" in script, (case, script)
    assert "Apple Notes" in _answer(result) and "workspace fallback" not in _answer(result), (case, _answer(result)[:300])
    assert served_notes.notes.scripts == [], f"{case}: the create touched other notes: {served_notes.notes.scripts}"
    assert _workspace_notes(served_notes.workspace) == [], f"{case}: a workspace note was written too"


# ----------------------------------------------------------------------------------------- controls and near misses

# (case, wording, workspace notes written): requests that name no Apple Notes store stay in the workspace.
_WORKSPACE_WORDINGS = [
    ("control-workspace-find", "find my notes about packaging", 0),
    ("control-workspace-save", 'save a note titled "Kickoff" with: call the venue', 1),
    ("control-workspace-create", 'create a note titled "Trip" with: pack chargers', 1),
    ("near-miss-quoted-create-title", 'create a note titled "Apple Notes export" with: steps', 1),
    ("near-miss-payload-names-the-app", 'save a note titled "Groceries" with: export this to Apple Notes later', 1),
    ("near-miss-app-words-in-the-topic", "find notes about the Apple note migration", 0),
]


@pytest.mark.parametrize(("case", "text", "written"), _WORKSPACE_WORDINGS, ids=[row[0] for row in _WORKSPACE_WORDINGS])
def test_a_request_that_names_no_apple_notes_store_stays_in_the_workspace(served_notes, case, text, written):
    result = _served(text, served_notes)
    _assert_reached_notes(result, case)
    assert served_notes.notes.scripts == [] and served_notes.creates == [], (
        f"{case}: reached Apple Notes: {served_notes.notes.scripts + served_notes.creates}")
    assert len(_workspace_notes(served_notes.workspace)) == written, (case, _workspace_notes(served_notes.workspace), _answer(result)[:300])


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("adversarial-web-search", "search the web for Apple Notes tips"),
        ("adversarial-how-clause", "look up how Apple Notes syncs across devices"),
        ("near-miss-how-to-question", "how do I find an Apple note?"),
    ],
)
def test_a_sentence_about_apple_notes_that_reads_no_note_never_reaches_the_notes_bridge(served_notes, case, text):
    _served(text, served_notes)
    assert served_notes.notes.scripts == [] and served_notes.creates == [], (
        f"{case}: reached Apple Notes: {served_notes.notes.scripts + served_notes.creates}")
    assert _workspace_notes(served_notes.workspace) == [], case


# ------------------------------------------------------------------------------------------------------ cross-turn


def test_a_workspace_note_and_an_apple_note_of_one_title_are_each_read_from_their_own_store(served_notes):
    """The workspace holds a note titled Plan after the first turn, so a find that still searched the workspace would
    show its body; the Apple read must show the Personal note, and the workspace show must still show its own."""
    env = served_notes
    with _session(env) as turn:
        saved = turn('save a note titled "Plan" with: workspace plan draft')
        _assert_reached_notes(saved, "cross-turn-workspace-save")
        assert len(_workspace_notes(env.workspace)) == 1 and env.notes.scripts == [] and env.creates == [], _answer(saved)[:300]

        found = turn('find my Apple note "Plan" in the Personal folder')
        _assert_reached_notes(found, "cross-turn-apple-find")
        assert "<div>home chores</div>" in _answer(found) and "workspace plan draft" not in _answer(found), _answer(found)[:300]
        scripts_after_apple_find = list(env.notes.scripts)

        shown = turn('show the note titled "Plan"')
        _assert_reached_notes(shown, "cross-turn-workspace-show")
        assert "workspace plan draft" in _answer(shown) and "<div>" not in _answer(shown), _answer(shown)[:300]
        assert env.notes.scripts == scripts_after_apple_find, "the workspace show reached Apple Notes"
    assert _changed(env.notes) == set() and env.creates == []


def test_an_apple_note_create_writes_nothing_the_workspace_search_can_find(served_notes):
    env = served_notes
    with _session(env) as turn:
        created = turn('In my Travel folder, create an Apple note titled "Trip" with: pack chargers')
        _assert_reached_notes(created, "cross-turn-create")
        assert len(env.creates) == 1 and 'at folder "Travel" of default account' in env.creates[0], env.creates

        searched = turn("find my notes about chargers")
        _assert_reached_notes(searched, "cross-turn-workspace-search")
        assert "No notes in this workspace match 'chargers'" in _answer(searched), _answer(searched)[:300]

        listed = turn("and find my Apple notes in the Travel folder")
        _assert_reached_notes(listed, "cross-turn-apple-listing")
        assert TRAVEL_PACK in _answer(listed) and LOCAL_PACK not in _answer(listed), _answer(listed)[:400]
    assert _workspace_notes(env.workspace) == [] and len(env.creates) == 1 and _changed(env.notes) == set()

"""A Notes request acts inside the folder and account it names, through the served turn.

Measured 2026-09-15 at d6a398af on the served path (`VoolAgent.run_once`, model stand-in, synthetic Notes runner).
Routing reached Notes, and the Notes owner then declined each of these without a write:
* 'rename my Apple note "Plan" in the Work folder of the iCloud account to "Plan v2"' -- the account was read as
  "Work folder of the iCloud";
* 'rename my apple note "Plan" in teh work folder to "Plan v2"' -- the folder was read as "teh work";
* 'show my Apple note "Plan" in the Work folder' -- the read kind did not narrow by folder ("2 notes share that title").
The same reader dropped a misspelled scope noun, read "the folder named Work" as the folder "the", read scope out of
quoted payloads, and let a quoted folder name become the new title. The create path read 'to Apple Notes in the Work
folder' as the folder "Apple Notes in the Work".

The scope is now read by `core.operator.apple_notes._ScopeReader`; its function words fold through `core.typo_fold`.
Every case is a real served turn in a fresh session (the delete confirmation continues its own session). Assertions
read the environment: the synthetic Notes store, the scripts the bridge received, and the route. LABELLED: model
stand-in, synthetic Notes runner (`FakeNotes`, never osascript), loopback CalDAV fixture from `served_env`.
"""
from __future__ import annotations

import contextlib
import copy
import re
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

pytestmark = [pytest.mark.pa_beta]

_MUTATING = ("make new paragraph", "set name of theNote", "delete theNote")
_WORKSPACE_FILE = "harbor_manifest.txt"

# Three notes titled "Plan": a dropped or misread folder OR account lands on the wrong note or refuses as ambiguous.
WORK_ICLOUD = "x-coredata://Note/work-icloud-plan"
WORK_MAC = "x-coredata://Note/work-mac-plan"
PERSONAL_ICLOUD = "x-coredata://Note/personal-icloud-plan"
_ACCOUNT_SEED = {
    WORK_ICLOUD: {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "<div>budget draft</div>"},
    WORK_MAC: {"title": "Plan", "folder": "Work", "account": "On My Mac", "body": "<div>offline copy</div>"},
    PERSONAL_ICLOUD: {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>home chores</div>"},
}

# One account; "Plan" in three folders, one of them named from a near miss of "the"; a note titled like a scope phrase.
WORK_PLAN = "x-coredata://Note/work-plan"
HOME_PLAN = "x-coredata://Note/home-plan"
THEO_PLAN = "x-coredata://Note/theo-plan"
LOCAL_PACK = "x-coredata://Note/local-pack"
TRAVEL_PACK = "x-coredata://Note/travel-pack"
ERRANDS_GROCERIES = "x-coredata://Note/errands-groceries"
ARCHIVE_WORK_FOLDER = "x-coredata://Note/archive-work-folder"
_FOLDER_SEED = {
    WORK_PLAN: {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "<div>budget draft</div>"},
    HOME_PLAN: {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>home chores</div>"},
    THEO_PLAN: {"title": "Plan", "folder": "Theo Studio", "account": "iCloud", "body": "<div>theo sketches</div>"},
    LOCAL_PACK: {"title": "Packing list", "folder": "Local", "account": "iCloud", "body": "<div>socks</div>"},
    TRAVEL_PACK: {"title": "Packing list", "folder": "Travel", "account": "iCloud", "body": "<div>maps</div>"},
    ERRANDS_GROCERIES: {"title": "Groceries", "folder": "Errands", "account": "iCloud", "body": "<div>eggs</div>"},
    ARCHIVE_WORK_FOLDER: {"title": "Work folder", "folder": "Archive", "account": "iCloud", "body": "<div>archived list</div>"},
}

# Novel data the repair never saw: another account name, a folder name with a number, a different title.
GMAIL_TAXES = "x-coredata://Note/gmail-taxes-receipts"
ICLOUD_TAXES = "x-coredata://Note/icloud-taxes-receipts"
GMAIL_HOME = "x-coredata://Note/gmail-home-receipts"
_NOVEL_SEED = {
    GMAIL_TAXES: {"title": "Receipts", "folder": "Taxes 2026", "account": "Gmail", "body": "<div>q1 filed</div>"},
    ICLOUD_TAXES: {"title": "Receipts", "folder": "Taxes 2026", "account": "iCloud", "body": "<div>q2 pending</div>"},
    GMAIL_HOME: {"title": "Receipts", "folder": "Home", "account": "Gmail", "body": "<div>q3 boiler</div>"},
}


@pytest.fixture
def served_notes(request, monkeypatch):
    workspace = request.getfixturevalue("served_env")["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / _WORKSPACE_FILE).write_text("crates: 12\n", encoding="utf-8")

    def install(seed):
        return FakeNotes(copy.deepcopy(seed)).install(monkeypatch)

    return workspace, install


@contextlib.contextmanager
def _session(workspace):
    """Real served turns in one fresh session, the model lane answered by the stand-in."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="notes-scope-reading", provider_id="notes-scope-reading",
        provider_name="stand-in", model_name="notes-scope-reading",
        output_text="MODEL STAND-IN: no general answer needed for this operator turn.",
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness(f"sess-notes-scope-reading-{uuid.uuid4().hex[:8]}")
    context = dict(_SOURCE_CONTEXT, workspace=str(workspace), operating_mode="auto")
    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            yield lambda text: harness.agent.run_once(text, source_context=dict(context), session_id_override=harness.session_id)
    finally:
        harness.close()


def _served(text, workspace):
    with _session(workspace) as turn:
        return turn(text)


def _answer(result):
    return str(result.get("response") or "")


def _assert_reached_notes(result, case):
    route = str(result.get("route") or "")
    assert route.startswith("action:operator_action"), f"{case}: never reached the Notes owner: route={route!r} answer={_answer(result)[:200]!r}"


def _changed(notes, seed):
    return {note_id for note_id, note in notes.notes.items() if note != seed.get(note_id)} | (set(seed) - set(notes.notes))


def _mutating_scripts(notes):
    return [script for script in notes.scripts if any(marker in script for marker in _MUTATING)]


def _assert_only(notes, seed, case, note_id, field, expected):
    assert notes.notes.get(note_id, {}).get(field) == expected, (case, notes.notes.get(note_id))
    assert _changed(notes, seed) == {note_id}, f"{case}: only the named note changes, changed={sorted(_changed(notes, seed))}"


def _assert_nothing_written(notes, seed, case):
    assert _mutating_scripts(notes) == [], f"{case}: a mutating script reached Notes: {_mutating_scripts(notes)}"
    assert _changed(notes, seed) == set(), f"{case}: the store changed: {sorted(_changed(notes, seed))}"


def _assert_shows_only(result, seed, body, case):
    answer = _answer(result)
    assert body in answer, f"{case}: the named note's body is not shown: {answer[:300]!r}"
    others = [note["body"] for note in seed.values() if note["body"] != body and note["body"] in answer]
    assert others == [], f"{case}: another note's body was shown: {others}"


# ------------------------------------------------------------------------------ the three promoted wordings


def test_the_folder_of_account_rename_renames_only_the_note_in_that_folder_and_account(served_notes):
    workspace, install = served_notes
    notes = install(_ACCOUNT_SEED)
    result = _served('rename my Apple note "Plan" in the Work folder of the iCloud account to "Plan v2"', workspace)
    _assert_reached_notes(result, "original")
    _assert_only(notes, _ACCOUNT_SEED, "original", WORK_ICLOUD, "title", "Plan v2")


def test_the_mistyped_folder_rename_renames_only_the_note_in_that_folder(served_notes):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served('rename my apple note "Plan" in teh work folder to "Plan v2"', workspace)
    _assert_reached_notes(result, "original")
    _assert_only(notes, _FOLDER_SEED, "original", WORK_PLAN, "title", "Plan v2")


def test_the_folder_scoped_show_shows_the_work_note_and_writes_nothing(served_notes):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served('show my Apple note "Plan" in the Work folder', workspace)
    _assert_reached_notes(result, "original")
    _assert_shows_only(result, _FOLDER_SEED, "<div>budget draft</div>", "original")
    _assert_nothing_written(notes, _FOLDER_SEED, "original")


# --------------------------------------------------------------------- family 1: the account beside a folder

# (case, text, the one note that changes, field, expected value)
_ACCOUNT_FAMILY = [
    ("paraphrase-possessive", "rename my Apple note \"Plan\" in my iCloud account's Work folder to \"Plan v2\"", WORK_ICLOUD, "title", "Plan v2"),
    ("paraphrase-scope-first", 'in the iCloud account, rename the Apple note "Plan" in the Work folder to "Plan v2"', WORK_ICLOUD, "title", "Plan v2"),
    ("paraphrase-append-on-my", 'append to my Apple note "Plan" in the Work folder on my iCloud account with "sent to finance"',
     WORK_ICLOUD, "body", "<div>budget draft</div>\nsent to finance"),
    ("paraphrase-from-under", 'rename my Apple note "Plan" from the Work folder under the iCloud account to "Plan v2"', WORK_ICLOUD, "title", "Plan v2"),
    ("paraphrase-folder-named", 'rename my Apple note "Plan" in the folder named Work of the iCloud account to "Plan v2"', WORK_ICLOUD, "title", "Plan v2"),
    ("sloppy-no-determiners", 'rename apple note "Plan" in work folder of icloud account to "Plan v2"', WORK_ICLOUD, "title", "Plan v2"),
    ("sloppy-shouted", 'RENAME MY APPLE NOTE "Plan" IN THE WORK FOLDER OF THE ICLOUD ACCOUNT TO "Plan v2"', WORK_ICLOUD, "title", "Plan v2"),
    ("sloppy-noun-typos-filler", 'pls rename my apple note "Plan" in the Work fodler of the iCloud acount to "Plan v2" thx', WORK_ICLOUD, "title", "Plan v2"),
    ("sloppy-quoted-names", 'rename my Apple note "Plan" in the "Work" folder of the "iCloud" account to "Plan v2"', WORK_ICLOUD, "title", "Plan v2"),
    ("sloppy-fragment-abbreviation", 'can u rename my apple note "Plan" (Work folder, iCloud acct) to "Plan v2"?', WORK_ICLOUD, "title", "Plan v2"),
    ("control-other-account", 'rename my Apple note "Plan" in the Work folder of the On My Mac account to "Plan v2"', WORK_MAC, "title", "Plan v2"),
    ("near-miss-scope-inside-payload", 'append to my Apple note "Plan" in the Personal folder with "move it to the Work folder of the On My Mac account"',
     PERSONAL_ICLOUD, "body", "<div>home chores</div>\nmove it to the Work folder of the On My Mac account"),
]


@pytest.mark.parametrize(("case", "text", "note_id", "field", "expected"), _ACCOUNT_FAMILY, ids=[row[0] for row in _ACCOUNT_FAMILY])
def test_an_account_named_beside_a_folder_scopes_the_effect(served_notes, case, text, note_id, field, expected):
    workspace, install = served_notes
    notes = install(_ACCOUNT_SEED)
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    _assert_only(notes, _ACCOUNT_SEED, case, note_id, field, expected)


# (case, text, what the refusal says): the Notes owner refuses and writes nothing.
_ACCOUNT_REFUSALS = [
    ("control-no-account-named", 'rename my Apple note "Plan" in the Work folder to "Plan v2"', "2 notes share that title"),
    ("control-account-without-the-note", 'rename my Apple note "Plan" in the Work folder of the Gmail account to "Plan v2"', "no note titled"),
]


@pytest.mark.parametrize(("case", "text", "said"), _ACCOUNT_REFUSALS, ids=[row[0] for row in _ACCOUNT_REFUSALS])
def test_a_scope_that_does_not_single_out_one_note_writes_nothing(served_notes, case, text, said):
    workspace, install = served_notes
    notes = install(_ACCOUNT_SEED)
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    assert said in _answer(result), (case, _answer(result)[:300])
    _assert_nothing_written(notes, _ACCOUNT_SEED, case)


# --------------------------------------------------------------------------- family 2: mistyped scope words

_TYPO_FAMILY = [
    ("paraphrase-clean", 'rename my Apple note "Plan" in the Work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-scope-first", 'in the Work folder, rename my Apple note "Plan" to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-from", 'rename my Apple note "Plan" from the Work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("paraphrase-inside-append", 'append to my Apple note "Packing list" inside the Local folder with "passport and charger"',
     LOCAL_PACK, "body", "<div>socks</div>\npassport and charger"),
    ("paraphrase-folder-called", 'rename my Apple note "Plan" in the folder called Work to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-transposed-article", 'rename my apple note "Plan" in hte work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-preposition-and-article", 'rename my apple note "Plan" form teh work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-noun-typo-append", 'append to my apple note "Packing list" in teh local fodler with "passport and charger"',
     LOCAL_PACK, "body", "<div>socks</div>\npassport and charger"),
    ("sloppy-shouted", 'RENAME MY APPLE NOTE "Plan" IN TEH WORK FOLDER TO "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-possessive-typo", 'rename my apple note "Plan" in yuor work folder to "Plan v2"', WORK_PLAN, "title", "Plan v2"),
    ("sloppy-dropped-preposition", 'rename my apple note "Plan" teh work folder to "Plan v2"!!', WORK_PLAN, "title", "Plan v2"),
    # "Theo" is one insertion from "the": folded, it would end the name at "Studio" and miss the folder.
    ("control-capitalised-name", 'rename my apple note "Plan" in Theo Studio folder to "Plan v2"', THEO_PLAN, "title", "Plan v2"),
    ("near-miss-typos-inside-payload", 'append to my apple note "Groceries" with "put teh eggs in teh work folder"',
     ERRANDS_GROCERIES, "body", "<div>eggs</div>\nput teh eggs in teh work folder"),
    ("near-miss-typo-in-new-title", 'rename my apple note "Plan" in teh work folder to "teh plan"', WORK_PLAN, "title", "teh plan"),
]


@pytest.mark.parametrize(("case", "text", "note_id", "field", "expected"), _TYPO_FAMILY, ids=[row[0] for row in _TYPO_FAMILY])
def test_a_mistyped_scope_phrase_scopes_the_effect(served_notes, case, text, note_id, field, expected):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    _assert_only(notes, _FOLDER_SEED, case, note_id, field, expected)


def test_a_mistyped_folder_name_is_not_corrected_into_another_folder(served_notes):
    """The typo budget spends only on the reader's own words: "wrok" names no folder, so nothing is renamed."""
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served('rename my apple note "Plan" in the wrok folder to "Plan v2"', workspace)
    _assert_reached_notes(result, "control-name-typo")
    assert 'no note titled "Plan" was found in wrok' in _answer(result), _answer(result)[:300]
    _assert_nothing_written(notes, _FOLDER_SEED, "control-name-typo")


# Unquoted data that reads like a scope phrase: the handler already read it as the payload or the new name.
_DATA_NEAR_MISSES = [
    ("near-miss-unquoted-payload", 'append to my apple note "Groceries" with move the eggs into the work folder',
     ERRANDS_GROCERIES, "body", "<div>eggs</div>\nmove the eggs into the work folder"),
    ("near-miss-unquoted-new-name", 'rename my Apple note "Groceries" to Groceries in the Travel folder',
     ERRANDS_GROCERIES, "title", "Groceries in the Travel folder"),
]


@pytest.mark.parametrize(("case", "text", "note_id", "field", "expected"), _DATA_NEAR_MISSES, ids=[row[0] for row in _DATA_NEAR_MISSES])
def test_unquoted_data_that_reads_like_a_scope_names_no_scope(served_notes, case, text, note_id, field, expected):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    _assert_only(notes, _FOLDER_SEED, case, note_id, field, expected)


# ------------------------------------------------------------------------------ family 3: the read kind

# (case, text, the body shown)
_READ_FAMILY = [
    ("paraphrase-open-from", 'open my Apple note "Plan" from the Work folder', "<div>budget draft</div>"),
    ("paraphrase-relative-clause", 'read the Apple note "Plan" that is in my Work folder', "<div>budget draft</div>"),
    ("paraphrase-display-account", 'display my Apple note "Plan" in the Work folder of the iCloud account', "<div>budget draft</div>"),
    ("paraphrase-inside-travel", 'show the Apple note "Packing list" inside my Travel folder', "<div>maps</div>"),
    ("paraphrase-folder-named", 'show my Apple note "Plan" in the folder named Work', "<div>budget draft</div>"),
    ("sloppy-bare", 'show my apple note "Plan" in work folder', "<div>budget draft</div>"),
    ("sloppy-shouted", 'SHOW MY APPLE NOTE "Plan" IN THE WORK FOLDER', "<div>budget draft</div>"),
    ("sloppy-typos-filler", 'open apple note "Plan" in teh work fodler pls', "<div>budget draft</div>"),
    ("sloppy-parenthetical", 'show my apple note "Plan" (work folder)', "<div>budget draft</div>"),
    ("sloppy-quoted-folder", 'read my apple note "Plan" in the "Work" folder', "<div>budget draft</div>"),
    ("sloppy-question-marks", 'yo show apple note "Packing list" in the travel folder??', "<div>maps</div>"),
    ("sloppy-scope-first-no-comma", 'in the Personal folder open my Apple note "Plan"', "<div>home chores</div>"),
    ("control-unique-title-unscoped", 'show my Apple note "Groceries"', "<div>eggs</div>"),
    ("near-miss-title-reads-like-scope", 'show my Apple note "Work folder"', "<div>archived list</div>"),
]


@pytest.mark.parametrize(("case", "text", "body"), _READ_FAMILY, ids=[row[0] for row in _READ_FAMILY])
def test_a_scoped_read_shows_the_note_in_that_scope(served_notes, case, text, body):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    _assert_shows_only(result, _FOLDER_SEED, body, case)
    _assert_nothing_written(notes, _FOLDER_SEED, case)


def test_a_scope_first_read_split_at_its_comma_reaches_the_notes_owner(served_notes):
    """Measured at d6a398af and 4f18cfdb: the execution grain cut 'in the Personal folder,' from 'open my Apple note
    "Plan"' (a leading fragment never rode the request after it), and the turn ended as
    deterministic:demand_owned_mixed_turn, 'could not be completed'. The grain law is pinned in
    tests/test_leading_fragment_rides_the_request_after_it.py."""
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served('in the Personal folder, open my Apple note "Plan"', workspace)
    _assert_reached_notes(result, "scope-first-read")
    _assert_shows_only(result, _FOLDER_SEED, "<div>home chores</div>", "scope-first-read")
    _assert_nothing_written(notes, _FOLDER_SEED, "scope-first-read")


_SEEDS = {"folder": _FOLDER_SEED, "account": _ACCOUNT_SEED, "novel": _NOVEL_SEED}

# (case, seed, text, the body shown): a scope that LEADS a request whose verb opens a demand. Every title here is
# held by more than one note, so a dropped scope refuses as ambiguous or shows another note's body.
_SCOPE_FIRST_FAMILY = [
    ("paraphrase-show", "folder", 'in the Personal folder, show my Apple note "Plan"', "<div>home chores</div>"),
    ("paraphrase-from", "folder", 'From the Travel folder, open my Apple note "Packing list"', "<div>maps</div>"),
    ("paraphrase-inside-possessive", "folder", 'Inside my Work folder, show the Apple note "Plan"', "<div>budget draft</div>"),
    ("paraphrase-account-then-folder", "novel", 'In the Gmail account, in the Taxes 2026 folder, open my Apple note "Receipts"',
     "<div>q1 filed</div>"),
    ("paraphrase-account-display", "account", 'in the On My Mac account, display my Apple note "Plan"', "<div>offline copy</div>"),
    ("sloppy-dropped-preposition", "folder", 'Personal folder, open my Apple note "Plan"', "<div>home chores</div>"),
    ("sloppy-typo-polite", "folder", 'in teh travel folder, please show my apple note "Packing list"', "<div>maps</div>"),
    ("sloppy-shouted", "folder", 'IN THE WORK FOLDER, OPEN MY APPLE NOTE "Plan"', "<div>budget draft</div>"),
    ("sloppy-casual-filler", "folder", 'and in the personal folder, show me my apple note "Plan" pls', "<div>home chores</div>"),
    ("sloppy-spaced-comma", "folder", 'in the Travel folder , open my Apple note "Packing list"', "<div>maps</div>"),
]


@pytest.mark.parametrize(("case", "seed", "text", "body"), _SCOPE_FIRST_FAMILY, ids=[row[0] for row in _SCOPE_FIRST_FAMILY])
def test_a_scope_that_leads_the_request_scopes_the_read(served_notes, case, seed, text, body):
    workspace, install = served_notes
    notes = install(_SEEDS[seed])
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    _assert_shows_only(result, _SEEDS[seed], body, case)
    _assert_nothing_written(notes, _SEEDS[seed], case)


def test_a_scope_that_leads_a_create_addresses_that_folder(served_notes, monkeypatch):
    workspace, _install = served_notes
    scripts = _create_runner(monkeypatch)
    result = _served('In my Travel folder, create a note in Apple Notes titled "Trip" with: pack chargers', workspace)
    _assert_reached_notes(result, "scope-first-create")
    assert len(scripts) == 1 and 'at folder "Travel" of default account' in scripts[0], (scripts, _answer(result)[:300])


def test_scope_first_follow_ups_in_one_session_each_reach_the_notes_owner(served_notes):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    with _session(workspace) as turn:
        listing = turn("show my Apple Notes in the Travel folder")
        _assert_reached_notes(listing, "cross-turn-listing")
        first = turn('in the Personal folder, open my Apple note "Plan"')
        _assert_reached_notes(first, "cross-turn-first")
        _assert_shows_only(first, _FOLDER_SEED, "<div>home chores</div>", "cross-turn-first")
        follow_up = turn('and in the Work folder, show me the Apple note "Plan" too')
        _assert_reached_notes(follow_up, "cross-turn-follow-up")
        _assert_shows_only(follow_up, _FOLDER_SEED, "<div>budget draft</div>", "cross-turn-follow-up")
    _assert_nothing_written(notes, _FOLDER_SEED, "cross-turn")


def test_a_topic_coordinated_before_a_notes_request_is_not_swallowed_by_it(served_notes):
    """Negative control for the grain law: 'the Ukraine situation, and ...' coordinates a second request. It stays its own
    unit, so the operator lane never ends the turn alone while the topic vanishes under the note."""
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served('the Ukraine situation, and open my Apple note "Plan" in the Personal folder', workspace)
    route = str(result.get("route") or "")
    assert not route.startswith("action:operator_action"), (route, _answer(result)[:400])
    _assert_nothing_written(notes, _FOLDER_SEED, "coordinated-topic")


_READ_REFUSALS = [
    ("control-unscoped-same-title", 'show my Apple note "Plan"', "3 notes share that title"),
    ("control-title-not-in-that-folder", 'show my Apple note "Plan" in the Travel folder', 'no note titled "Plan" was found in Travel'),
]


@pytest.mark.parametrize(("case", "text", "said"), _READ_REFUSALS, ids=[row[0] for row in _READ_REFUSALS])
def test_a_read_that_does_not_single_out_one_note_shows_no_body(served_notes, case, text, said):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served(text, workspace)
    _assert_reached_notes(result, case)
    answer = _answer(result)
    assert said in answer, (case, answer[:300])
    assert not [note["body"] for note in _FOLDER_SEED.values() if note["body"] in answer], (case, answer[:300])
    _assert_nothing_written(notes, _FOLDER_SEED, case)


# ---------------------------------------------------------------------------------------- list, create, novel


def test_a_scoped_listing_lists_only_that_folder(served_notes):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served("show my Apple Notes in the Travel folder", workspace)
    _assert_reached_notes(result, "list")
    answer = _answer(result)
    assert "1 note(s) in the Travel folder" in answer and TRAVEL_PACK in answer, answer[:400]
    assert not [note_id for note_id in _FOLDER_SEED if note_id != TRAVEL_PACK and note_id in answer], answer[:400]
    _assert_nothing_written(notes, _FOLDER_SEED, "list")


def _create_runner(monkeypatch):
    from core.operator import apple_notes

    scripts = []

    def runner(command, **kwargs):
        scripts.append(command[-1])
        return SimpleNamespace(returncode=0, stdout=f"note id x-coredata://Note/created-{len(scripts)}", stderr="")

    real_create = apple_notes.create_apple_note
    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real_create(**kw, runner=runner))
    return scripts


def test_a_create_addresses_the_folder_and_account_it_names(served_notes, monkeypatch):
    workspace, _install = served_notes
    scripts = _create_runner(monkeypatch)
    result = _served('save a note to Apple Notes in the Work folder of the iCloud account titled "Trip" with: pack chargers', workspace)
    _assert_reached_notes(result, "create")
    assert len(scripts) == 1 and 'at folder "Work" of account "iCloud"' in scripts[0], scripts


def test_a_create_body_that_reads_like_scope_is_not_a_destination(served_notes, monkeypatch):
    workspace, _install = served_notes
    scripts = _create_runner(monkeypatch)
    result = _served('save a note to Apple Notes titled "Moving" with: put the boxes into the garage folder', workspace)
    _assert_reached_notes(result, "create-near-miss")
    assert len(scripts) == 1 and "make new note with properties" in scripts[0], scripts
    assert "put the boxes into the garage folder" in scripts[0], scripts


def test_a_novel_wording_with_different_data_acts_inside_its_scope(served_notes):
    workspace, install = served_notes
    notes = install(_NOVEL_SEED)
    appended = _served('append to my Apple note "Receipts" inside the Taxes 2026 folder of my Gmail account with "dentist invoice"', workspace)
    _assert_reached_notes(appended, "novel-append")
    _assert_only(notes, _NOVEL_SEED, "novel-append", GMAIL_TAXES, "body", "<div>q1 filed</div>\ndentist invoice")

    shown = _served("open my Apple note \"Receipts\" from my Gmail account's Home folder", workspace)
    _assert_reached_notes(shown, "novel-read")
    assert "<div>q3 boiler</div>" in _answer(shown), _answer(shown)[:300]
    assert "q1 filed" not in _answer(shown) and "q2 pending" not in _answer(shown), _answer(shown)[:300]


# ---------------------------------------------------------------------------------------------- cross-turn


def test_a_scoped_delete_confirmation_carries_the_scope_and_deletes_only_that_note(served_notes):
    workspace, install = served_notes
    notes = install(_ACCOUNT_SEED)
    with _session(workspace) as turn:
        asked = turn('delete my Apple note "Plan" in the Work folder of the iCloud account')
        assert asked.get("route") == "action:operator_action_approval_required", asked.get("route")
        _assert_nothing_written(notes, _ACCOUNT_SEED, "delete-asks-first")
        offered = re.search(r'Reply "(.+)" to confirm', _answer(asked))
        assert offered, _answer(asked)[:400]
        confirmation = offered.group(1)

        confirmed = turn(confirmation)
        _assert_reached_notes(confirmed, "delete-confirmed")
    assert WORK_ICLOUD not in notes.notes, f"the note in Work of iCloud was not deleted by {confirmation!r}"
    assert _changed(notes, _ACCOUNT_SEED) == {WORK_ICLOUD}, "the other two notes titled Plan are untouched"


# ---------------------------------------------------------------------------------------- workspace controls

_WORKSPACE_CONTROLS = [
    ("control-work-folder", "what's in the work folder?", "deterministic:folder_overview_fast_path"),
    ("control-which-folder", "which folder am I in?", "deterministic:workspace_identity_fast_path"),
    ("control-count-python-files", "count how many python files are in this project", "deterministic:workspace_measurement_fast_path"),
]


@pytest.mark.parametrize(("case", "text", "route"), _WORKSPACE_CONTROLS, ids=[row[0] for row in _WORKSPACE_CONTROLS])
def test_workspace_folder_questions_keep_the_workspace_lanes(served_notes, case, text, route):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served(text, workspace)
    assert result.get("route") == route, (case, result.get("route"), _answer(result)[:160])
    assert notes.scripts == [], f"{case}: never reaches the Notes bridge"

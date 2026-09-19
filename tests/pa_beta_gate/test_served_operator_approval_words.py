"""A request approves an action only in its own words, through the served turn.

The operator parser read an approval word anywhere in a request, inside quoted values and inside longer words, as a
substring (the parser seam lives in tests/test_operator_approval_words.py). The Notes delete handler asks for confirmation
only when a request is not approved, and the move, temp-cleanup and calendar-draft handlers run a pending action when a
request approves it. Measured on this served path at 4f18cfdb: 'delete my Apple note "Proceeds 2026"' deleted the note on
the first turn, and so did every title holding an approval word, a folder named "Proceed", an unquoted title and "from
yesterday"; 'move "Proceeds 2026.xlsx" to "Finance"' ran the earlier pending move, 'clean temp files in "Do it later"' ran
the pending cleanup, a draft titled "Proceeds review" was created without the balanced preview, and 'is calendar <id>
approved?' created that draft.

The follow-up proceed reading had the same flaw. After a question, the offered confirmation 'yes, delete the apple note
"Yes list" in the Go ahead folder' read "go ahead" out of the folder name, so the question turn was resumed and the
confirmation dropped (measured at the first parser-only repair).

Every case is a real served turn in a fresh session; multi-turn cases continue one session. Assertions read the
environment: the synthetic Notes store and the scripts the bridge received, the calendar outbox, the moved file, the temp
files, and the route. The Notes turns carry what the Auto composer mode sends (operating mode and autonomy "auto"), the
move and cleanup turns what Manual sends (no autonomy override, so the saved hands-off preference), and the calendar
draft turns a balanced autonomy. LABELLED: model stand-in, synthetic Notes runner (`FakeNotes`, never osascript), loopback
CalDAV fixture from `served_env`; the moved file and the temp root live in pytest's tmp_path, and a cleanup of any other
root raises before anything is deleted.
"""
from __future__ import annotations

import contextlib
import copy
import re
import uuid
from pathlib import Path
from unittest import mock

import pytest

from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

pytestmark = [pytest.mark.pa_beta]

_MUTATING = ("make new paragraph", "set name of theNote", "delete theNote")
_MARKERS = ["approve", "go ahead", "do it", "proceed", "yes", "fuck it", "clean all", "delete all", "remove all"]
_AUTO = {"operating_mode": "auto", "autonomy_override": "auto"}
_MANUAL = {"operating_mode": "manual"}
_BALANCED = {"operating_mode": "manual", "autonomy_override": "balanced"}
_HANDS_OFF = {"operating_mode": "manual", "autonomy_override": "hands_off"}


def _slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _note(title, folder="Notes"):
    return {"title": title, "folder": folder, "account": "iCloud", "body": f"<div>{_slug(title)} in {folder}</div>"}


def _id(title, folder=""):
    return "x-coredata://Note/" + _slug(f"{title} {folder}".strip())


# Every marker as a whole word and inside a longer word, the three reported titles, and titles the repair never saw.
_WHOLE = {marker: f"{marker.capitalize()} list" for marker in _MARKERS}
_INSIDE = {marker: f"{marker.capitalize()}s" for marker in _MARKERS}
_REPORTED = ["Proceeds 2026", "Do it later", "Eyes on Q3"]
_NOVEL = ["Cargo ahead of schedule", "Undo itinerary edits", "Disapproved expenses", "Yesterday's standup"]
_SEED = {_id(title): _note(title) for title in [*_WHOLE.values(), *_INSIDE.values(), *_REPORTED, *_NOVEL, "Groceries"]}
# Folder names holding approval words, beside same-titled notes elsewhere.
_SCOPED = {
    _id("Plan", "Proceed"): _note("Plan", "Proceed"),
    _id("Plan", "Go ahead"): _note("Plan", "Go ahead"),
    _id("Plan", "Work"): _note("Plan", "Work"),
    _id("Yes list", "Go ahead"): _note("Yes list", "Go ahead"),
    _id("Yes list", "Personal"): _note("Yes list", "Personal"),
    _id("Budget", "Personal"): _note("Budget", "Personal"),
}


@pytest.fixture
def served_notes(request, monkeypatch):
    workspace = request.getfixturevalue("served_env")["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)

    def install(seed):
        return FakeNotes(copy.deepcopy(seed)).install(monkeypatch)

    return workspace, install


@contextlib.contextmanager
def _session(workspace, context):
    """Real served turns in one fresh session, the model lane answered by the stand-in."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="approval-words", provider_id="approval-words", provider_name="stand-in",
        model_name="approval-words", output_text="MODEL STAND-IN: no general answer needed for this operator turn.",
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness(f"sess-approval-words-{uuid.uuid4().hex[:8]}")
    source_context = dict(_SOURCE_CONTEXT, workspace=str(workspace), **context)
    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            yield lambda text, turn_context=None: harness.agent.run_once(
                text, source_context=dict(source_context, **(turn_context or {})), session_id_override=harness.session_id)
    finally:
        harness.close()


def _served(text, workspace, context=_AUTO):
    with _session(workspace, context) as turn:
        return turn(text)


def _answer(result):
    return str(result.get("response") or "")


def _changed(notes, seed):
    return {note_id for note_id, note in notes.notes.items() if note != seed.get(note_id)} | (set(seed) - set(notes.notes))


def _mutating_scripts(notes):
    return [script for script in notes.scripts if any(marker in script for marker in _MUTATING)]


def _assert_asked_first(result, notes, seed, case):
    route = result.get("route")
    assert route == "action:operator_action_approval_required", (case, route, _answer(result)[:300])
    assert "to confirm" in _answer(result), (case, _answer(result)[:300])
    assert _mutating_scripts(notes) == [], f"{case}: a mutating script reached Notes: {_mutating_scripts(notes)}"
    assert _changed(notes, seed) == set(), f"{case}: the store changed: {sorted(_changed(notes, seed))}"


def _assert_deleted_only(result, notes, seed, case, note_id):
    route = str(result.get("route") or "")
    assert route.startswith("action:operator_action") and route != "action:operator_action_approval_required", (
        case, route, _answer(result)[:300])
    assert note_id not in notes.notes, f"{case}: {note_id} was not deleted: {_answer(result)[:300]}"
    assert _changed(notes, seed) == {note_id}, f"{case}: only that note is deleted, changed={sorted(_changed(notes, seed))}"


# ------------------------------------------------------------------------ titles holding each approval word

_TITLE_CASES = [
    *[(f"whole-word-{_slug(marker)}", title) for marker, title in _WHOLE.items()],
    *[(f"inside-a-word-{_slug(marker)}", title) for marker, title in _INSIDE.items()],
    *[(f"reported-{_slug(title)}", title) for title in _REPORTED],
    *[(f"novel-{_slug(title)}", title) for title in _NOVEL],
]


@pytest.mark.parametrize(("case", "title"), _TITLE_CASES, ids=[row[0] for row in _TITLE_CASES])
def test_a_title_holding_an_approval_word_is_asked_about_not_deleted(served_notes, case, title):
    workspace, install = served_notes
    notes = install(_SEED)
    _assert_asked_first(_served(f'delete my Apple note "{title}"', workspace), notes, _SEED, case)


# (case, request): the same delete worded other ways; only the title holds an approval word.
_WORDINGS = [
    ("paraphrase-remove-the", 'remove the Apple note "Proceeds 2026"'),
    ("paraphrase-please-for-me", 'please delete the Apple note "Do it later" for me'),
    ("paraphrase-i-want-to", 'I want to delete my Apple note "Eyes on Q3"'),
    ("paraphrase-named", 'delete the Apple note named "Go ahead list"'),
    ("paraphrase-could-you", 'could you delete my Apple note "Disapproved expenses" now'),
    ("sloppy-filler", 'pls delete apple note "Eyes on Q3" thx'),
    ("sloppy-shouted", 'DELETE MY APPLE NOTE "PROCEEDS 2026"'),
    ("sloppy-curly-quotes", "delete apple note “Do it later”"),
    ("sloppy-single-quotes", "delete my apple note 'Yes list'"),
    ("sloppy-question-marks", 'can u remove my apple note "Cargo ahead of schedule"??'),
    ("sloppy-no-determiner", 'delete apple note "Undo itinerary edits"'),
]


@pytest.mark.parametrize(("case", "text"), _WORDINGS, ids=[row[0] for row in _WORDINGS])
def test_a_delete_worded_any_way_asks_first_when_only_the_title_holds_the_word(served_notes, case, text):
    workspace, install = served_notes
    notes = install(_SEED)
    _assert_asked_first(_served(text, workspace), notes, _SEED, case)


# (case, request, seed): the approval word sits in an unquoted title, a folder name, or a longer word of the request.
_NEAR_MISSES = [
    ("unquoted-title", "delete my apple note titled Go ahead list", _SEED),
    ("word-holding-yes", 'delete my Apple note "Groceries" from yesterday', _SEED),
    ("word-holding-yes-in-parentheses", 'delete my Apple note "Groceries" (eyes only)', _SEED),
    ("folder-name", 'delete my Apple note "Plan" in the Proceed folder', _SCOPED),
    ("quoted-folder-name", 'delete my Apple note "Plan" in the "Go ahead" folder', _SCOPED),
]


@pytest.mark.parametrize(("case", "text", "seed"), _NEAR_MISSES, ids=[row[0] for row in _NEAR_MISSES])
def test_an_approval_word_inside_a_name_or_a_longer_word_asks_first(served_notes, case, text, seed):
    workspace, install = served_notes
    notes = install(seed)
    _assert_asked_first(_served(text, workspace), notes, seed, case)


# (case, request, the one note deleted): the request's own approval, beside titles holding the same words.
_APPROVED = [
    ("yes-comma", 'yes, delete my Apple note "Eyes on Q3"', _id("Eyes on Q3")),
    ("go-ahead-and", 'go ahead and delete my Apple note "Proceeds 2026"', _id("Proceeds 2026")),
    ("do-it-after-the-title", 'delete my Apple note "Do it later", do it', _id("Do it later")),
    ("proceed-colon", 'proceed: delete my apple note "Disapproved expenses"', _id("Disapproved expenses")),
    ("shouted-yes-no-comma", 'YES delete apple note "Yes list"', _id("Yes list")),
    ("approve-dash", 'approve - delete my Apple note "Approve list"', _id("Approve list")),
]


@pytest.mark.parametrize(("case", "text", "note_id"), _APPROVED, ids=[row[0] for row in _APPROVED])
def test_the_requests_own_approval_deletes_exactly_that_note(served_notes, case, text, note_id):
    workspace, install = served_notes
    notes = install(_SEED)
    _assert_deleted_only(_served(text, workspace), notes, _SEED, case, note_id)


# ---------------------------------------------------------------------------------------------- cross-turn


def _offered(result):
    offered = re.search(r'Reply "(.+)" to confirm', _answer(result))
    assert offered, _answer(result)[:400]
    return offered.group(1)


def test_the_offered_confirmation_deletes_exactly_the_note_it_names(served_notes):
    workspace, install = served_notes
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        asked = turn('delete my Apple note "Eyes on Q3"')
        _assert_asked_first(asked, notes, _SEED, "ask")
        confirmed = turn(_offered(asked))
    _assert_deleted_only(confirmed, notes, _SEED, "confirmed", _id("Eyes on Q3"))


def test_a_confirmation_naming_a_folder_that_holds_an_approval_word_deletes_only_that_note(served_notes):
    workspace, install = served_notes
    notes = install(_SCOPED)
    with _session(workspace, _AUTO) as turn:
        asked = turn('delete my Apple note "Yes list" in the Go ahead folder')
        _assert_asked_first(asked, notes, _SCOPED, "scoped-ask")
        confirmation = _offered(asked)
        assert "Go ahead" in confirmation, confirmation
        confirmed = turn(confirmation)
    _assert_deleted_only(confirmed, notes, _SCOPED, "scoped-confirmed", _id("Yes list", "Go ahead"))


def test_a_request_after_the_question_naming_a_go_ahead_folder_runs_as_its_own_request(served_notes):
    workspace, install = served_notes
    notes = install(_SCOPED)
    with _session(workspace, _AUTO) as turn:
        # The question names no approval word, so this case fails at 4f18cfdb only through the follow-up proceed reading.
        _assert_asked_first(turn('delete my Apple note "Budget" in the Personal folder'), notes, _SCOPED, "ask")
        renamed = turn('rename my Apple note "Plan" in the Go ahead folder to "Plan v2"')
    assert notes.notes[_id("Plan", "Go ahead")]["title"] == "Plan v2", (renamed.get("route"), _answer(renamed)[:300])
    assert _changed(notes, _SCOPED) == {_id("Plan", "Go ahead")}, sorted(_changed(notes, _SCOPED))


def test_repeating_the_request_or_answering_a_bare_yes_deletes_nothing(served_notes):
    workspace, install = served_notes
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _assert_asked_first(turn('delete my Apple note "Proceeds 2026"'), notes, _SEED, "ask")
        _assert_asked_first(turn('delete my Apple note "Proceeds 2026"'), notes, _SEED, "repeated")
        bare = [turn(text) for text in ("yes", "go ahead")]
    assert _mutating_scripts(notes) == [] and _changed(notes, _SEED) == set(), [(r.get("route"), _answer(r)[:200]) for r in bare]


def test_an_approved_request_after_the_question_runs_as_that_request(served_notes):
    workspace, install = served_notes
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _assert_asked_first(turn('delete my Apple note "Groceries"'), notes, _SEED, "ask")
        approved = turn('go ahead and delete my Apple note "Groceries"')
    _assert_deleted_only(approved, notes, _SEED, "approved-after-question", _id("Groceries"))


def test_an_unquoted_title_holding_a_proceed_word_after_the_question_is_its_own_request(served_notes):
    workspace, install = served_notes
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _assert_asked_first(turn('delete my Apple note "Groceries"'), notes, _SEED, "ask")
        asked = turn("delete my apple note titled Go ahead list")
    assert '"Go ahead list"' in _answer(asked), (asked.get("route"), _answer(asked)[:300])
    _assert_asked_first(asked, notes, _SEED, "second-ask")


def test_edits_to_a_note_whose_title_holds_an_approval_word_run_without_a_question(served_notes):
    workspace, install = served_notes
    notes = install(_SEED)
    appended = _served('append to my Apple note "Yes list" with "call the venue"', workspace)
    assert notes.notes[_id("Yes list")]["body"].endswith("\ncall the venue"), (appended.get("route"), _answer(appended)[:300])
    renamed = _served('rename my Apple note "Do it later" to "Do it now"', workspace)
    assert notes.notes[_id("Do it later")]["title"] == "Do it now", (renamed.get("route"), _answer(renamed)[:300])
    assert _changed(notes, _SEED) == {_id("Yes list"), _id("Do it later")}, sorted(_changed(notes, _SEED))


# -------------------------------------------------------------------------------- sibling kinds, served


def _drafts():
    from core.runtime_paths import data_path

    return sorted(Path(data_path("calendar_outbox")).glob("*.ics"))


def test_a_meeting_title_holding_an_approval_word_is_previewed_not_created(served_notes):
    workspace, _install = served_notes
    previewed = _served('schedule a meeting "Proceeds review" on 2026-09-21 15:30 for 45m', workspace, _BALANCED)
    assert "Meeting preview ready." in _answer(previewed), (previewed.get("route"), _answer(previewed)[:300])
    assert _drafts() == [], "balanced autonomy previews first; a title's words approve nothing"
    created = _served('schedule a meeting "Go ahead sync" on 2026-09-22 15:30 for 45m, yes', workspace, _BALANCED)
    assert "Calendar event created." in _answer(created), (created.get("route"), _answer(created)[:300])
    drafts = _drafts()
    assert len(drafts) == 1 and "SUMMARY:Go ahead sync" in drafts[0].read_text(encoding="utf-8"), drafts


def _assert_not_an_operator_turn(result, case):
    route = str(result.get("route") or "")
    assert not route.startswith("action:operator_action"), (case, route, _answer(result)[:300])


def test_a_question_about_a_meeting_draft_does_not_approve_it(served_notes):
    workspace, _install = served_notes
    with _session(workspace, _BALANCED) as turn:
        previewed = turn('schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m')
        action_id = re.search(r"approve calendar (\S+)", _answer(previewed))
        assert action_id, (previewed.get("route"), _answer(previewed)[:300])
        # Under hands-off autonomy the gate asks for no approval: selecting the draft's kind alone would create it.
        for question, context in ((f"is calendar {action_id.group(1)} approved?", None),
                                  (f"has the meeting {action_id.group(1)} been approved yet", _HANDS_OFF)):
            asked = turn(question, context)
            _assert_not_an_operator_turn(asked, question)
            assert _drafts() == [], (question, asked.get("route"), _answer(asked)[:300])
        approved = turn(f"approve calendar {action_id.group(1)}")
    assert "Calendar event created." in _answer(approved) and len(_drafts()) == 1, (approved.get("route"), _answer(approved)[:300])


def _pending_move(turn, tmp_path):
    desk, archive = tmp_path / "desk", tmp_path / "archive"
    desk.mkdir()
    archive.mkdir()
    source = desk / "report.txt"
    source.write_text("q3 numbers", encoding="utf-8")
    previewed = turn(f'move "{source}" to "{archive}"')
    assert "Move preview ready." in _answer(previewed), (previewed.get("route"), _answer(previewed)[:300])
    return source, archive / "report.txt", _answer(previewed).strip().split()[-1]


def test_a_later_move_quoting_an_approval_word_does_not_run_the_pending_move(served_notes, tmp_path):
    workspace, _install = served_notes
    with _session(workspace, _MANUAL) as turn:
        source, moved, action_id = _pending_move(turn, tmp_path)
        for text in ('move "Proceeds 2026.xlsx" to "Finance"', 'move "Yes votes.csv" into "Approve later"'):
            answer = _answer(turn(text))
            assert source.exists() and not moved.exists(), f"{text!r} ran the pending move: {answer[:300]}"
            assert "still needs explicit approval" in answer, (text, answer[:300])
        approved = turn(f"approve move {action_id}")
    assert "Move finished." in _answer(approved), _answer(approved)[:300]
    assert moved.exists() and not source.exists()


def test_a_question_about_a_pending_move_does_not_run_it(served_notes, tmp_path):
    workspace, _install = served_notes
    with _session(workspace, _MANUAL) as turn:
        source, moved, action_id = _pending_move(turn, tmp_path)
        # Under Auto the gate asks for no approval: selecting the move's kind alone would run it.
        for question, context in ((f"was move {action_id} approved?", None), (f"has move {action_id} been approved?", _AUTO)):
            asked = turn(question, context)
            _assert_not_an_operator_turn(asked, question)
            assert source.exists() and not moved.exists(), (question, asked.get("route"), _answer(asked)[:300])


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """A temp root inside tmp_path is the only cleanup root; cleaning any other root raises before deleting."""
    from core import local_operator_actions

    root = tmp_path / "temp"
    (root / "nested").mkdir(parents=True)
    (root / "one.tmp").write_bytes(b"a" * 1024)
    (root / "nested" / "two.tmp").write_bytes(b"b" * 2048)
    for name in ("TMP", "TEMP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TMPDIR", str(root))
    monkeypatch.setattr(local_operator_actions.tempfile, "gettempdir", lambda: str(root))
    real_delete = local_operator_actions._delete_children

    def delete_inside_tmp_path_only(path):
        if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError(f"refusing to clean {path}: outside this test's tmp_path")
        return real_delete(path)

    monkeypatch.setattr(local_operator_actions, "_delete_children", delete_inside_tmp_path_only)
    return root


def test_a_later_cleanup_request_holding_an_approval_word_does_not_run_the_pending_cleanup(served_notes, temp_root):
    workspace, _install = served_notes
    before = sorted(path.name for path in temp_root.rglob("*"))
    with _session(workspace, _MANUAL) as turn:
        previewed = turn(f'find disk bloat in "{temp_root}"')
        assert "Safe temp cleanup preview" in _answer(previewed), (previewed.get("route"), _answer(previewed)[:400])
        for text in ('clean temp files in "Do it later"', "clean allotment temp files"):
            answer = _answer(turn(text))
            assert sorted(path.name for path in temp_root.rglob("*")) == before, f"{text!r} ran the pending cleanup: {answer[:300]}"
            assert "still needs explicit approval" in answer, (text, answer[:300])
        cleaned = turn("clean all temp files")
    assert "Temp cleanup finished." in _answer(cleaned), _answer(cleaned)[:300]
    assert list(temp_root.iterdir()) == []

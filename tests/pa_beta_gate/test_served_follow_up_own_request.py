"""A follow-up that brings its own request runs as that request, through the served turn.

Measured 2026-09-15 at cbe05fa3 through VoolAgent.run_once with the synthetic Notes runner: after 'delete my Apple note
"Groceries"' asked for confirmation (its checkpoint stays pending_approval), 'go ahead and delete my Apple note
"Groceries"' was read as a go-ahead. core.agent_runtime.checkpoints.prepare_runtime_checkpoint resumed the question and
replaced the message with the stored request, so the delete was asked again and nothing was deleted. 'delete my apple note
titled Go ahead list' read "go ahead" out of its unquoted title and was dropped the same way: the reply was the Groceries
question.

Every case is a real served turn in one session opened by that question. Assertions read the synthetic Notes store, the
scripts the bridge received, and the route. LABELLED: model stand-in, synthetic Notes runner (`FakeNotes`, never
osascript), loopback CalDAV fixture from `served_env`; the turns carry what the Auto composer sends (operating mode and
autonomy "auto"). The seam tests live in tests/test_checkpoint_follow_up_own_request.py.
"""
from __future__ import annotations

import pytest

from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env  # noqa: F401 -- fixture
from tests.pa_beta_gate.test_served_operator_approval_words import (  # noqa: F401 -- served_notes is a fixture
    _AUTO,
    _SEED,
    _answer,
    _assert_asked_first,
    _assert_deleted_only,
    _changed,
    _id,
    _mutating_scripts,
    _session,
    served_notes,
)

pytestmark = [pytest.mark.pa_beta]

_QUESTION = 'delete my Apple note "Groceries"'


def _ask(turn, notes):
    asked = turn(_QUESTION)
    _assert_asked_first(asked, notes, _SEED, "question")
    assert '"Groceries"' in _answer(asked), _answer(asked)[:300]


def _assert_the_question_again(result, notes, case):
    _assert_asked_first(result, notes, _SEED, case)
    assert '"Groceries"' in _answer(result), (case, _answer(result)[:300])


# (case, follow-up, the one note it deletes): a follow-up approving a delete in its own words.
_APPROVED_REQUESTS = [
    ("reported", 'go ahead and delete my Apple note "Groceries"', "Groceries"),
    ("paraphrase-proceed-to", 'proceed to delete my Apple note "Groceries"', "Groceries"),
    ("paraphrase-please-for-me", 'please go ahead and delete the Apple note "Groceries" for me', "Groceries"),
    ("paraphrase-i-want-you-to", 'I want you to delete my Apple note "Groceries", go ahead', "Groceries"),
    ("paraphrase-ok-do-it-colon", 'ok do it: delete my Apple note "Groceries"', "Groceries"),
    ("paraphrase-trailing-do-it", 'delete my Apple note "Groceries" now, do it', "Groceries"),
    ("sloppy-no-conjunction", 'go ahead delete apple note "Groceries"', "Groceries"),
    ("sloppy-n-curly-quotes-pls", "go ahead n delete my apple note “Groceries” pls", "Groceries"),
    ("sloppy-again", 'go ahead and delete my apple note "Groceries" again', "Groceries"),
    ("sloppy-trailing-do-it-no-comma", 'delete apple note "Groceries" do it', "Groceries"),
    ("sloppy-yes-go-ahead-remove", 'yes go ahead remove my apple note "Groceries"', "Groceries"),
    ("another-note", 'go ahead and delete my Apple note "Eyes on Q3"', "Eyes on Q3"),
    ("another-note-title-holds-ahead", 'go ahead and delete my Apple note "Cargo ahead of schedule"', "Cargo ahead of schedule"),
]


@pytest.mark.parametrize(("case", "text", "title"), _APPROVED_REQUESTS, ids=[row[0] for row in _APPROVED_REQUESTS])
def test_a_follow_up_approving_its_own_delete_after_the_question_deletes_exactly_that_note(request, case, text, title):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        result = turn(text)
    _assert_deleted_only(result, notes, _SEED, case, _id(title))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "RESIDUAL, the execution grain's, identical as a first turn at cbe05fa3: 'go ahead and remove ...' is cut at 'and' "
        "because 'remove' is a demand head and 'delete' is not, so the go-ahead becomes a unit of its own and the delete unit "
        "asks (route deterministic:demand_owned_mixed_turn). The checkpoint seam runs this follow-up as its own request; the "
        "grain then separates the approval from the delete."
    ),
)
def test_a_follow_up_approving_a_remove_joined_by_and_deletes_that_note(request):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        result = turn('please go ahead and remove the Apple note "Groceries" for me')
    _assert_deleted_only(result, notes, _SEED, "remove-joined-by-and", _id("Groceries"))


def test_follow_ups_bringing_requests_of_other_kinds_run_them(request):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        renamed = turn('go ahead and rename my Apple note "Do it later" to "Do it now"')
        appended = turn('continue and append to my Apple note "Yes list" with "call the venue"')
    assert notes.notes[_id("Do it later")]["title"] == "Do it now", (renamed.get("route"), _answer(renamed)[:300])
    assert notes.notes[_id("Yes list")]["body"].endswith("\ncall the venue"), (appended.get("route"), _answer(appended)[:300])
    assert _changed(notes, _SEED) == {_id("Do it later"), _id("Yes list")}, sorted(_changed(notes, _SEED))


def test_a_follow_up_asking_for_other_work_is_answered_instead_of_the_question(request):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        answered = turn("go ahead and calculate 17 times 23")
    assert "391" in _answer(answered), (answered.get("route"), _answer(answered)[:300])
    assert "Groceries" not in _answer(answered), _answer(answered)[:300]
    assert _mutating_scripts(notes) == [] and _changed(notes, _SEED) == set()


# (case, follow-up, the title it names): an unquoted title holding a go-ahead word is a new request about that note.
_UNQUOTED_TITLES = [
    ("reported-titled", "delete my apple note titled Go ahead list", "Go ahead list"),
    ("named", "remove apple note named Do it later", "Do it later"),
    ("called", "delete my apple note called Proceed list", "Proceed list"),
]


@pytest.mark.parametrize(("case", "text", "title"), _UNQUOTED_TITLES, ids=[row[0] for row in _UNQUOTED_TITLES])
def test_an_unquoted_title_holding_a_go_ahead_word_after_the_question_is_asked_about(request, case, text, title):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        asked = turn(text)
    assert f'"{title}"' in _answer(asked) and "Groceries" not in _answer(asked), (case, _answer(asked)[:300])
    _assert_asked_first(asked, notes, _SEED, case)


# (case, follow-up): a go-ahead that points back at the pending question. It resumes the question, which asks again.
_GO_AHEADS = [
    ("bare-yes", "yes"),
    ("bare-go-ahead", "go ahead"),
    ("ok-do-it", "ok do it"),
    ("carry-on", "carry on"),
    ("yes-comma-go-ahead", "yes, go ahead"),
    ("please-just-do-it-now", "please just do it now"),
    ("delete-it", "go ahead and delete it"),
    ("proceed-with-the-delete", "proceed with the delete"),
    ("continue-with-that-note", "continue with that note"),
]


@pytest.mark.parametrize(("case", "text"), _GO_AHEADS, ids=[row[0] for row in _GO_AHEADS])
def test_a_go_ahead_with_nothing_of_its_own_asks_the_pending_question_again(request, case, text):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        again = turn(text)
    _assert_the_question_again(again, notes, case)


# (case, follow-up): the follow-up negates. Approval is read without polarity, so none of these may become a fresh,
# approved delete; each resumes the question as before.
_NEGATED = [
    ("no-dont-go-ahead", 'no, don\'t go ahead and delete my Apple note "Groceries"'),
    ("go-ahead-but-dont", 'go ahead but don\'t delete my Apple note "Groceries"'),
    ("reported-actually-dont-do-it", 'delete my Apple note "Groceries", actually don\'t do it'),
    ("not-now", 'not now, go ahead and delete my Apple note "Groceries"'),
]


@pytest.mark.parametrize(("case", "text"), _NEGATED, ids=[row[0] for row in _NEGATED])
def test_a_negating_follow_up_deletes_nothing(request, case, text):
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        result = turn(text)
    assert _mutating_scripts(notes) == [] and _changed(notes, _SEED) == set(), (case, result.get("route"), _answer(result)[:300])


def test_a_deferred_approval_after_the_question_deletes_nothing_yet(request):
    # Moved out of xfail on 2026-09-15: on the combined candidate the polarity reading (deferral
    # withholds approval) and the follow-up seam (its own request) compose -- 'go ahead and delete
    # my Apple note "Groceries" tomorrow' after the question asks again; nothing is deleted yet.
    workspace, install = request.getfixturevalue("served_notes")
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        _ask(turn, notes)
        turn('go ahead and delete my Apple note "Groceries" tomorrow')
    assert _mutating_scripts(notes) == [] and _changed(notes, _SEED) == set()

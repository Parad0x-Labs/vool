"""A request whose own words withhold its approval asks again, through the served turn.

The operator parser counted an approval word in a request's own words without reading what the request said about it
(the parser seam lives in tests/test_operator_approval_polarity.py). The Notes delete handler asks for confirmation only
when a request is not approved. The move, temp-cleanup and calendar-draft handlers run the session's pending action when
a request approves it and the autonomy gate asks for approval, and on the approval kind alone when the gate asks for
none. Measured at the parser at cbe05fa3: the reported 'delete my Apple note "Plan", actually don't do it' and 'do not
proceed, delete my apple note "Plan" later' parsed as approved, and so did "don't clean all temp files", "don't approve
move <id>" and "do not approve calendar <id>".

Every case is a real served turn in a fresh session; multi-turn cases continue one session. Assertions read the
environment: the synthetic Notes store and the scripts the bridge received, the calendar outbox, the moved file and the
temp files. The Notes turns carry what the Auto composer mode sends, the move and cleanup turns what Manual sends (the
saved hands-off preference, whose gate asks for approval of a move or cleanup), and the calendar draft turns a balanced
autonomy. The approval-id replies also run under Auto (move) and hands-off (calendar draft), where the kind alone runs the
pending action. LABELLED: model stand-in, synthetic Notes runner (`FakeNotes`, never osascript), loopback CalDAV fixture
from `served_env`; the moved file and the temp root live in pytest's tmp_path, and a cleanup of any other root raises
before anything is deleted (`temp_root`).
"""
from __future__ import annotations

import re

import pytest

from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env
from tests.pa_beta_gate.test_served_operator_approval_words import (
    _AUTO,
    _BALANCED,
    _HANDS_OFF,
    _MANUAL,
    _answer,
    _assert_asked_first,
    _assert_deleted_only,
    _changed,
    _drafts,
    _id,
    _mutating_scripts,
    _note,
    _offered,
    _pending_move,
    _served,
    _session,
    served_notes,
    temp_root,
)

pytestmark = [pytest.mark.pa_beta]

_TITLES = ["Plan", "Lisbon itinerary", "Vendor shortlist", "Garden plan", "Tax receipts 2025", "Do not disturb",
           "Later this week", "Wait list"]
_SEED = {_id(title): _note(title) for title in _TITLES}
_SCOPED = {_id("Plan", "Later"): _note("Plan", "Later"), _id("Plan", "Work"): _note("Plan", "Work")}


# ------------------------------------------------------------------------------------- Notes delete, first turn

# (case, request): the approval the request carries is negated, refused, doubted, retracted or deferred by its own words.
# Every member reaches the Notes delete as one request and deleted the note on the first turn at cbe05fa3 (served route
# census, evidence stage pol-census-base-01).
_WITHHELD = [
    ("reported-actually-dont-do-it", 'delete my Apple note "Plan", actually don\'t do it'),
    ("reported-do-not-proceed-later", 'do not proceed, delete my apple note "Plan" later'),
    ("paraphrase-never-approve", 'Never approve this: delete my Apple note "Lisbon itinerary"'),
    ("paraphrase-once-backed-up", 'Go ahead and delete my Apple note "Vendor shortlist" once I have backed it up'),
    ("paraphrase-not-sure", 'I am not sure about this, but go ahead and delete my Apple note "Garden plan"'),
    ("paraphrase-retracted", 'Yes, delete my Apple note "Garden plan". Actually, never mind.'),
    ("paraphrase-taken-back-with-a-pronoun", 'delete my Apple note "Garden plan", yes. Actually, don\'t delete it'),
    ("paraphrase-action-negated", 'go ahead, but don\'t remove my Apple note "Plan"'),
    ("sloppy-dont-no-comma", 'dont do it delete my apple note "Plan"'),
    ("sloppy-shouted-dash", 'DO NOT PROCEED - delete apple note "Plan"'),
    ("sloppy-filler-not-yet", 'pls delete apple note "Plan" but dont go ahead yet thx'),
    ("sloppy-no-wait", 'yes delete my apple note "Plan"... no wait'),
    ("sloppy-typo-tomorrow", 'go ahead n delete my apple note "Plan" tomorow'),
    ("sloppy-curly-apostrophe", "don’t go ahead, delete my apple note 'Plan'"),
    ("refused-bare-no", 'no, go ahead and delete my Apple note "Tax receipts 2025"'),
    ("negated-value", 'yes, delete my Apple note, not "Tax receipts 2025"'),
    ("deferred-not-now", 'go ahead and delete my Apple note "Tax receipts 2025", just not now'),
    ("deferred-clock-time", 'yes, delete my Apple note "Tax receipts 2025" at 6pm'),
    ("conditional", 'yes, delete my Apple note "Tax receipts 2025" if it is empty'),
    ("cancel-that", 'yes, delete my apple note "Tax receipts 2025", cancel that'),
]


@pytest.mark.parametrize(("case", "text"), _WITHHELD, ids=[row[0] for row in _WITHHELD])
def test_a_request_that_withholds_its_approval_is_asked_about_not_deleted(served_notes, case, text):
    workspace, install = served_notes
    notes = install(_SEED)
    _assert_asked_first(_served(text, workspace), notes, _SEED, case)


# (case, request): wordings a lane before the approval reading already refuses, at cbe05fa3 and at the fix alike: the turn's
# no-action policy ("do not delete", core/agent_runtime/intent_claims.py) and a demand split into parts. Controls: nothing
# changes, and they prove nothing about the approval reading.
_REFUSED_BEFORE_THE_APPROVAL_READING = [
    ("no-action-policy", 'Yes, but please do not delete my Apple note "Garden plan"'),
    ("split-demand-should-not", 'You should not go ahead and remove the Apple note "Lisbon itinerary"'),
    ("split-demand-until-i-say", 'Remove the Apple note "Vendor shortlist", but do not proceed until I say so'),
]


@pytest.mark.parametrize(("case", "text"), _REFUSED_BEFORE_THE_APPROVAL_READING,
                         ids=[row[0] for row in _REFUSED_BEFORE_THE_APPROVAL_READING])
def test_a_request_a_lane_refuses_before_the_approval_reading_changes_nothing(served_notes, case, text):
    workspace, install = served_notes
    notes = install(_SEED)
    result = _served(text, workspace)
    assert result.get("route") != "action:operator_action_executed", (case, result.get("route"), _answer(result)[:300])
    assert _mutating_scripts(notes) == [] and _changed(notes, _SEED) == set(), (case, result.get("route"), _answer(result)[:300])


# (case, request, the one note deleted): approvals nothing withholds, beside titles holding withholding words.
_STANDING = [
    ("offered-confirmation", 'yes, delete the apple note "Plan"', _id("Plan")),
    ("no-problem", 'no problem, go ahead and delete my Apple note "Lisbon itinerary"', _id("Lisbon itinerary")),
    ("dont-ask-again", 'yes, don\'t ask again, delete my Apple note "Vendor shortlist"', _id("Vendor shortlist")),
    ("dont-ask-again-after-the-request", 'delete my apple note "Garden plan" yes dont ask again', _id("Garden plan")),
    ("without-asking", 'go ahead without asking me, delete my Apple note "Tax receipts 2025"', _id("Tax receipts 2025")),
    ("negation-in-the-title", 'yes, delete my Apple note "Do not disturb"', _id("Do not disturb")),
    ("deferral-in-the-title", 'go ahead and delete my Apple note "Later this week"', _id("Later this week")),
    ("deferral-in-an-unquoted-title", "yes, delete my apple note titled Wait list", _id("Wait list")),
    ("now", 'proceed: delete my Apple note "Plan" now', _id("Plan")),
]


@pytest.mark.parametrize(("case", "text", "note_id"), _STANDING, ids=[row[0] for row in _STANDING])
def test_an_approval_nothing_withholds_deletes_exactly_that_note(served_notes, case, text, note_id):
    workspace, install = served_notes
    notes = install(_SEED)
    _assert_deleted_only(_served(text, workspace), notes, _SEED, case, note_id)


def test_a_deferral_word_in_a_folder_name_defers_nothing(served_notes):
    workspace, install = served_notes
    notes = install(_SCOPED)
    result = _served('yes, delete my Apple note "Plan" in the Later folder', workspace)
    _assert_deleted_only(result, notes, _SCOPED, "later-folder", _id("Plan", "Later"))


# --------------------------------------------------------------------------------------- Notes delete, cross-turn

# (case, reply after the question): a reply that withholds the approval, including the offered confirmation itself.
_WITHHELD_REPLIES = [
    ("dont-do-it", "don't do it"),
    ("no-do-not-proceed", "no, do not proceed"),
    ("go-ahead-later", "go ahead later"),
    ("offered-then-not-yet", "{offered}... actually, not yet"),
    ("offered-tomorrow", "{offered} tomorrow"),
    ("offered-then-never-mind", "{offered}. never mind"),
    ("offered-then-taken-back", "{offered}. Actually, don't delete it"),
]


@pytest.mark.parametrize(("case", "reply"), _WITHHELD_REPLIES, ids=[row[0] for row in _WITHHELD_REPLIES])
def test_a_withheld_reply_to_the_question_deletes_nothing_and_the_offered_confirmation_still_deletes(served_notes, case, reply):
    workspace, install = served_notes
    notes = install(_SEED)
    with _session(workspace, _AUTO) as turn:
        asked = turn('delete my Apple note "Vendor shortlist"')
        _assert_asked_first(asked, notes, _SEED, "ask")
        offered = _offered(asked)
        withheld = turn(reply.format(offered=offered))
        assert _mutating_scripts(notes) == [] and _changed(notes, _SEED) == set(), (
            case, withheld.get("route"), _answer(withheld)[:300])
        confirmed = turn(offered)
    _assert_deleted_only(confirmed, notes, _SEED, case, _id("Vendor shortlist"))


# ------------------------------------------------------------------------------------------ move, cleanup, calendar

# (case, reply after the move preview, per-turn context): the reply withholds its approval. Each ran the pending move at
# cbe05fa3. A move request that ends in "go ahead" is left out: the follow-up proceed reading resumes the preview turn on
# it before the request is parsed (the approval-words lane's residual, repaired in its own lane).
_WITHHELD_MOVES = [
    ("negated-approve", "don't approve move {action_id}", None),
    ("deferred-approve", "approve move {action_id} after lunch", None),
    ("refused-approve", "approve move {action_id}? no", None),
    ("deferred-request", 'yes, move "{source}" to "{archive}" tomorrow', None),
    ("action-taken-back", 'move "{source}" to "{archive}", go ahead. Actually, don\'t move it', None),
    ("negated-approve-under-auto", "dont approve move {action_id}", _AUTO),
    ("deferred-approve-under-auto", "approve move {action_id} once I check the folder", _AUTO),
]


@pytest.mark.parametrize(("case", "template", "context"), _WITHHELD_MOVES, ids=[row[0] for row in _WITHHELD_MOVES])
def test_a_move_reply_that_withholds_its_approval_does_not_run_the_pending_move(served_notes, tmp_path, case, template, context):
    workspace, _install = served_notes
    with _session(workspace, _MANUAL) as turn:
        source, moved, action_id = _pending_move(turn, tmp_path)
        text = template.format(action_id=action_id, source=source, archive=moved.parent)
        answer = _answer(turn(text, context))
        assert source.exists() and not moved.exists(), f"{case}: {text!r} ran the pending move: {answer[:300]}"
        approved = turn(f"approve move {action_id}")
    assert "Move finished." in _answer(approved) and moved.exists() and not source.exists(), (case, _answer(approved)[:300])


# (case, reply after the cleanup preview): the reply withholds its approval.
_WITHHELD_CLEANUPS = [
    ("reported", "don't clean all temp files"),
    ("deferred", "clean all temp files later"),
    ("sloppy-not-yet", "pls dont clean all temp files yet"),
    ("doubt", "clean all temp files? not sure"),
    ("action-taken-back", "clean all temp files. Actually, don't remove them"),
    ("conditional-shouted", "FUCK IT, CLEAN ALL TEMP FILES IF THE BACKUP IS DONE"),
]


@pytest.mark.parametrize(("case", "text"), _WITHHELD_CLEANUPS, ids=[row[0] for row in _WITHHELD_CLEANUPS])
def test_a_cleanup_reply_that_withholds_its_approval_does_not_run_the_pending_cleanup(served_notes, temp_root, case, text):
    workspace, _install = served_notes
    before = sorted(path.name for path in temp_root.rglob("*"))
    with _session(workspace, _MANUAL) as turn:
        previewed = turn(f'find disk bloat in "{temp_root}"')
        assert "Safe temp cleanup preview" in _answer(previewed), (previewed.get("route"), _answer(previewed)[:400])
        answer = _answer(turn(text))
        assert sorted(path.name for path in temp_root.rglob("*")) == before, f"{case}: {text!r} ran the pending cleanup: {answer[:300]}"
        assert "still needs explicit approval" in answer, (case, answer[:300])
        cleaned = turn("clean all temp files")
    assert "Temp cleanup finished." in _answer(cleaned) and list(temp_root.iterdir()) == [], (case, _answer(cleaned)[:300])


def test_a_cleanup_the_no_action_policy_refuses_before_the_approval_reading_cleans_nothing(served_notes, temp_root):
    """Control: "do not remove" is refused by the turn's no-action policy at cbe05fa3 and at the fix alike."""
    workspace, _install = served_notes
    before = sorted(path.name for path in temp_root.rglob("*"))
    with _session(workspace, _MANUAL) as turn:
        turn(f'find disk bloat in "{temp_root}"')
        answer = _answer(turn("yes, but do not remove temp files"))
    assert sorted(path.name for path in temp_root.rglob("*")) == before, answer[:300]


def test_a_meeting_request_that_withholds_its_approval_is_previewed_and_its_own_time_defers_nothing(served_notes):
    workspace, _install = served_notes
    for text in ('schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m, but don\'t go ahead',
                 'yes, schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m once Ana confirms'):
        previewed = _served(text, workspace, _BALANCED)
        assert "Meeting preview ready." in _answer(previewed), (text, previewed.get("route"), _answer(previewed)[:300])
        assert _drafts() == [], text
    # The time a meeting takes is the meeting's time, not a deferral of the approval.
    created = _served('schedule a meeting "Ops Sync" tomorrow at 10am, yes', workspace, _BALANCED)
    assert "Calendar event created." in _answer(created), (created.get("route"), _answer(created)[:300])
    assert len(_drafts()) == 1, _drafts()


def test_a_withheld_approval_of_a_meeting_draft_does_not_create_it(served_notes):
    workspace, _install = served_notes
    with _session(workspace, _BALANCED) as turn:
        previewed = turn('schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m')
        action_id = re.search(r"approve calendar (\S+)", _answer(previewed))
        assert action_id, (previewed.get("route"), _answer(previewed)[:300])
        # Under hands-off the gate asks for no approval: selecting the draft's kind alone would create it.
        for text, context in ((f"do not approve calendar {action_id.group(1)}", None),
                              (f"don't approve calendar {action_id.group(1)}", _HANDS_OFF),
                              (f"approve calendar {action_id.group(1)} next week", _HANDS_OFF)):
            asked = turn(text, context)
            assert _drafts() == [], (text, asked.get("route"), _answer(asked)[:300])
        approved = turn(f"approve calendar {action_id.group(1)}")
    assert "Calendar event created." in _answer(approved) and len(_drafts()) == 1, (approved.get("route"), _answer(approved)[:300])


# ------------------------------------------------------------------------ the approval kind alone, under Auto (residual)

_KIND_ALONE_UNDER_AUTO = (
    "RESIDUAL, outside the approval reading: under Auto the cleanup and move gates ask for no approval, so "
    "core/operator/handlers.py runs the session's pending action on the kind alone and never reads the approval flag. The "
    "parser still selects that kind for a request whose own words negate it ('don't clean all temp files' is "
    "cleanup_temp_files; a move that names a path is move_path). Measured served at the fix; recorded in "
    "VOOL-DELIVERY/CURRENT_STATE.json operator_approval_polarity_2026_09_15."
)


@pytest.mark.xfail(strict=True, reason=_KIND_ALONE_UNDER_AUTO)
def test_under_auto_a_cleanup_request_that_negates_itself_does_not_run_the_pending_cleanup(served_notes, temp_root):
    workspace, _install = served_notes
    before = sorted(path.name for path in temp_root.rglob("*"))
    with _session(workspace, _MANUAL) as turn:
        previewed = turn(f'find disk bloat in "{temp_root}"')
        assert "Safe temp cleanup preview" in _answer(previewed), (previewed.get("route"), _answer(previewed)[:400])
        answer = _answer(turn("don't clean all temp files", _AUTO))
    assert sorted(path.name for path in temp_root.rglob("*")) == before, f"the pending cleanup ran: {answer[:300]}"


@pytest.mark.xfail(strict=True, reason=_KIND_ALONE_UNDER_AUTO)
def test_under_auto_a_move_request_that_negates_itself_does_not_run_the_pending_move(served_notes, tmp_path):
    workspace, _install = served_notes
    with _session(workspace, _MANUAL) as turn:
        source, moved, _action_id = _pending_move(turn, tmp_path)
        answer = _answer(turn(f'move "{source}" to "{moved.parent}", but don\'t go ahead', _AUTO))
    assert source.exists() and not moved.exists(), f"the pending move ran: {answer[:300]}"

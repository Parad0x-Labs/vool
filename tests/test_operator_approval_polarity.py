"""An approval word approves only when the request's own words do not withhold it.

Measured 2026-09-15 at cbe05fa3, the approval-words repair: `core.operator.parser._approval_requested` counted an approval
word whole in the request's own words and never read what the request said about it. These parsed as approved:
'delete my Apple note "Plan", actually don't do it', 'do not proceed, delete my apple note "Plan" later', "don't clean all
temp files", 'yes, don't delete my apple note "Plan"', 'delete my apple note "Plan", yes. never mind', and every deferral
("later", "tomorrow", "once I confirm", "not yet"). "don't approve move <id>" and "do not approve calendar <id>" also
selected the approval kind, which runs the pending action on the kind alone when the autonomy gate asks for no approval.

The reading fails closed: a request that negates, refuses, doubts, retracts or defers the approval or the action it names
asks again. These are seam tests of the parser; the served effects are in
tests/pa_beta_gate/test_served_operator_approval_polarity.py.
"""
from __future__ import annotations

import pytest

from core.operator import parser
from core.operator.parser import parse_operator_action_intent

_ID = "5e6f7a8b-9999-4aaa-8bbb-cccc0000dddd"


def _intent(text, kind):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind, (text, getattr(intent, "kind", None))
    return intent


def _holds_an_approval_word(text):
    """The first half of every withheld case: an approval word is one of the request's own words, so nothing but what the
    request says about it can withhold it (a case without one would pass whether or not polarity is read)."""
    return parser._APPROVAL_WORDS_RE.search(parser.request_own_words(text)) is not None


# (case, request): the Notes delete whose own words withhold the approval they carry.
_WITHHELD_DELETES = [
    # the reported wordings
    ("reported-actually-dont-do-it", 'delete my Apple note "Plan", actually don\'t do it'),
    ("reported-do-not-proceed-later", 'do not proceed, delete my apple note "Plan" later'),
    # clean paraphrases: other approval words, vocabulary and sentence shapes
    ("paraphrase-never-approve", 'Never approve this: delete my Apple note "Lisbon itinerary"'),
    ("paraphrase-should-not-go-ahead", 'You should not go ahead and remove the Apple note "Lisbon itinerary"'),
    ("paraphrase-until-i-say", 'Remove the Apple note "Vendor shortlist", but do not proceed until I say so'),
    ("paraphrase-once-backed-up", 'Go ahead and delete my Apple note "Vendor shortlist" once I have backed it up'),
    ("paraphrase-tomorrow", 'Delete my Apple note "Vendor shortlist" tomorrow; yes, go ahead then'),
    ("paraphrase-not-sure", 'I am not sure about this, but go ahead and delete my Apple note "Garden plan"'),
    ("paraphrase-retracted", 'Yes, delete my Apple note "Garden plan". Actually, never mind.'),
    ("paraphrase-action-negated", 'Yes, but please do not delete my Apple note "Garden plan"'),
    ("action-taken-back-with-a-pronoun", 'delete my Apple note "Garden plan", yes. Actually, don\'t delete it'),
    # sloppy: dropped punctuation and apostrophes, typos, shouting, fragments
    ("sloppy-dont-no-comma", 'dont do it delete my apple note "Plan"'),
    ("sloppy-shouted-dash", 'DO NOT PROCEED - delete apple note "Plan"'),
    ("sloppy-filler-not-yet", 'pls delete apple note "Plan" but dont go ahead yet thx'),
    ("sloppy-no-wait", 'yes delete my apple note "Plan"... no wait'),
    ("sloppy-typo-tomorrow", 'go ahead n delete my apple note "Plan" tomorow'),
    ("sloppy-curly-apostrophe", "don’t go ahead, delete my apple note 'Plan'"),
    ("sloppy-question-then-no", 'delete my apple note "Plan"? approve? no'),
    # unrelated titles, one withholding shape each
    ("refused-bare-no", 'no, go ahead and delete my Apple note "Tax receipts 2025"'),
    ("negated-value", 'yes, delete my Apple note, not "Tax receipts 2025"'),
    ("deferred-not-right-now", 'go ahead and delete my Apple note "Tax receipts 2025", just not right now'),
    ("deferred-not-now", 'yes, delete my Apple note "Tax receipts 2025", just not now'),
    ("deferred-clock-time", 'yes, delete my Apple note "Tax receipts 2025" at 6pm'),
    ("deferred-relative-delay", 'go ahead: delete my Apple note "Tax receipts 2025" in 20 minutes'),
    ("deferred-in-a-bit", 'proceed and delete my Apple note "Tax receipts 2025" in a bit'),
    ("conditional", 'yes, delete my Apple note "Tax receipts 2025" if it is empty'),
    ("wait-then", 'wait for the backup, then go ahead and delete my Apple note "Tax receipts 2025"'),
    ("hold-off", 'hold off, I will approve it later: delete my apple note "Tax receipts 2025"'),
    ("elliptical-dont", 'delete my Apple note "Tax receipts 2025" -- yes -- actually no, don\'t'),
    ("cancel-that", 'yes, delete my apple note "Tax receipts 2025", cancel that'),
    ("doubt-not-ok", "delete my apple note 'Tax receipts 2025', go ahead? not ok"),
    ("fuck-it-then-no", 'fuck it... no, don\'t delete my Apple note "Tax receipts 2025"'),
]


@pytest.mark.parametrize(("case", "text"), _WITHHELD_DELETES, ids=[row[0] for row in _WITHHELD_DELETES])
def test_a_request_that_withholds_its_approval_does_not_approve_the_notes_delete(case, text):
    assert _holds_an_approval_word(text), (case, text)
    assert _intent(text, "apple_note_delete").approval_requested is False, (case, text)


# (case, request): approvals nothing in the request withholds, including withholding words that sit inside values.
_STANDING_DELETES = [
    ("offered-confirmation", 'yes, delete the apple note "Plan"'),
    ("offered-confirmation-with-scope", 'yes, delete the apple note "Plan" in the Work folder of the iCloud account'),
    ("no-problem", 'no problem, go ahead and delete my Apple note "Plan"'),
    ("dont-ask-again", 'yes, don\'t ask again, delete my Apple note "Plan"'),
    ("dont-ask-again-after-the-request", 'delete my Apple note "Plan", yes, don\'t ask again'),
    ("dont-ask-again-sloppy", 'delete my apple note "Plan" yes dont ask again'),
    ("dont-ask-but-go-ahead", 'don\'t ask me but go ahead and delete my Apple note "Plan"'),
    ("no-problem-shouted", 'NO PROBLEM! GO AHEAD, DELETE MY APPLE NOTE "PLAN"'),
    ("without-asking", 'go ahead without asking me, delete my Apple note "Plan"'),
    ("no-need-to-ask", 'no need to ask, just delete my Apple note "Plan", yes'),
    ("no-matter-what", 'no matter what, delete my Apple note "Plan" -- do it'),
    ("dont-worry", "don't worry about me, yes, delete my apple note 'Plan'"),
    ("never-mind-before-the-request", 'never mind the preview, delete my Apple note "Plan", go ahead'),
    ("now", 'yes, delete my Apple note "Plan" now'),
    ("negation-in-a-quoted-title", 'yes, delete my Apple note "Do not disturb"'),
    ("deferral-in-a-quoted-title", 'go ahead and delete my Apple note "Later this week"'),
    ("deferral-in-an-unquoted-title", "yes, delete my apple note titled Wait list"),
    ("negation-in-a-folder-name", 'go ahead, delete my Apple note "Plan" in the Never Again folder'),
    ("deferral-in-a-folder-name", 'yes, delete my Apple note "Plan" in the Later folder'),
]


@pytest.mark.parametrize(("case", "text"), _STANDING_DELETES, ids=[row[0] for row in _STANDING_DELETES])
def test_an_approval_nothing_withholds_still_approves_the_notes_delete(case, text):
    assert _intent(text, "apple_note_delete").approval_requested is True, (case, text)


# (case, request, kind): the sibling kinds that read the same approval.
_WITHHELD_SIBLINGS = [
    ("cleanup-reported", "don't clean all temp files", "cleanup_temp_files"),
    ("cleanup-later", "clean all temp files later", "cleanup_temp_files"),
    ("cleanup-not-yet", "please do not clean all temp files yet", "cleanup_temp_files"),
    ("cleanup-shouted-tomorrow", "CLEAN ALL TEMP FILES TOMORROW MORNING", "cleanup_temp_files"),
    ("cleanup-action-negated", "yes, but dont remove temp files", "cleanup_temp_files"),
    ("cleanup-doubt", "clean all temp files? not sure", "cleanup_temp_files"),
    ("cleanup-after", "fuck it, clean all temp files after the backup finishes", "cleanup_temp_files"),
    ("move-negated", 'move "report.txt" to "archive", but don\'t go ahead', "move_path"),
    ("move-deferred-weekday", 'go ahead and move "report.txt" to "archive" on friday', "move_path"),
    ("move-value-negated", 'yes, move "report.txt" to "archive", not "drafts.txt"', "move_path"),
    ("schedule-negated", 'schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m, but don\'t go ahead', "schedule_calendar_event"),
    ("schedule-conditional", 'yes, schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m if Ana confirms', "schedule_calendar_event"),
    ("propose-deferred", 'yes, propose a meeting "Ops Sync" tomorrow at 10, but wait until I check', "propose_calendar_event"),
    # the action taken back with a pronoun: only the negation reaching the action's own verb withholds these
    ("cleanup-action-taken-back", "clean all temp files. Actually, don't remove them", "cleanup_temp_files"),
    ("move-action-taken-back", 'move "report.txt" to "archive", go ahead. Actually, don\'t move it', "move_path"),
    ("schedule-action-taken-back", 'schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m, yes. Actually, don\'t schedule it', "schedule_calendar_event"),
    ("propose-action-taken-back", 'yes, propose a meeting "Ops Sync" tomorrow at 10. Actually, don\'t book it', "propose_calendar_event"),
]


@pytest.mark.parametrize(("case", "text", "kind"), _WITHHELD_SIBLINGS, ids=[row[0] for row in _WITHHELD_SIBLINGS])
def test_sibling_kinds_do_not_approve_what_the_request_withholds(case, text, kind):
    assert _holds_an_approval_word(text), (case, text)
    assert _intent(text, kind).approval_requested is False, (case, text)


# (case, request, kind): sibling approvals nothing withholds. A time the calendar kinds take is the event's time, never
# a deferral of the approval.
_STANDING_SIBLINGS = [
    ("cleanup-control", "clean all temp files", "cleanup_temp_files"),
    ("cleanup-no-need-to-ask", "yes, clean all temp files, no need to ask", "cleanup_temp_files"),
    ("cleanup-dont-ask-again", "clean all temp files - don't ask me again", "cleanup_temp_files"),
    ("move-control", 'move "report.txt" to "archive", go ahead', "move_path"),
    ("move-withholding-words-in-values", 'no problem, move "Q3 later.xlsx" to "Not now", go ahead', "move_path"),
    ("schedule-iso-time", 'schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m, yes', "schedule_calendar_event"),
    ("schedule-tomorrow-clock", 'schedule a meeting "Ops Sync" tomorrow at 10am, yes', "schedule_calendar_event"),
    ("schedule-weekday-clock", 'yes, schedule a meeting "Ops Sync" monday at 9:30am', "schedule_calendar_event"),
    ("propose-tomorrow", 'yes, propose a meeting "Ops Sync" tomorrow at 10', "propose_calendar_event"),
    ("propose-relative-delay", 'go ahead and propose a call "Budget review" in 2 hours', "propose_calendar_event"),
]


@pytest.mark.parametrize(("case", "text", "kind"), _STANDING_SIBLINGS, ids=[row[0] for row in _STANDING_SIBLINGS])
def test_sibling_approvals_nothing_withholds_still_approve(case, text, kind):
    assert _intent(text, kind).approval_requested is True, (case, text)


@pytest.mark.parametrize(
    "text",
    [
        f"don't approve move {_ID}",
        f"do not approve calendar {_ID}",
        f"approve move {_ID} tomorrow",
        f"approve calendar {_ID}? no",
        f"never approve the meeting {_ID}",
        f"approve move {_ID} once the backup is done",
        f"DONT APPROVE MOVE {_ID}",
    ],
)
def test_a_withheld_approve_selects_no_approval_kind(text):
    """The kind alone runs a pending action when the autonomy gate asks for no approval (a move under Auto, a calendar draft
    under hands-off), so an "approve" the request withholds must not select the kind, not merely arrive unapproved."""
    assert parser._APPROVE_WORD_RE.search(parser.request_own_words(text)), text
    assert parse_operator_action_intent(text) is None, text


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"approve move {_ID}", "move_path"),
        (f"approve calendar {_ID}", "schedule_calendar_event"),
        (f"no problem, approve move {_ID}", "move_path"),
        (f"yes approve calendar {_ID} and don't ask again", "schedule_calendar_event"),
    ],
)
def test_an_approve_nothing_withholds_still_selects_and_approves_its_kind(text, kind):
    intent = _intent(text, kind)
    assert (intent.action_id, intent.approval_requested) == (_ID, True), text

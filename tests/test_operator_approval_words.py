"""Approval words count only in a request's own words.

Measured 2026-09-15 at 4f18cfdb: `core.operator.parser` read `approval_requested = any(marker in lowered for marker in
_APPROVAL_HINTS)` over the whole request, quoted values included, as a substring. 'delete my Apple note "Proceeds 2026"',
'delete my Apple note "Do it later"' and 'delete my Apple note "Eyes on Q3"' parsed as approved, and the Notes delete
handler asks for confirmation only when a request is not approved. The same reading approved an unquoted title ('titled Go
ahead list'), a folder name ('in the Proceed folder'), a word that only contains a marker ('from yesterday'), and the
schedule, propose, cleanup and move kinds whose quoted values hold a marker. 'is calendar <id> approved?' selected the
calendar approval kind and approved it.

These are seam tests of the parser. The served effects live in tests/pa_beta_gate/test_served_operator_approval_words.py.
"""
from __future__ import annotations

import pytest

from core.operator.apple_notes import parse_notes_destination
from core.operator.parser import parse_operator_action_intent

# The parser's approval words, as a user types them.
_MARKERS = ["approve", "go ahead", "do it", "proceed", "yes", "fuck it", "clean all", "delete all", "remove all"]
# The ones that confirm a single named action (the other three also name what a cleanup removes).
_CONFIRMING = ["approve", "go ahead", "do it", "proceed", "yes", "fuck it"]


def _intent(text, kind):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind, (text, getattr(intent, "kind", None))
    return intent


# (shape, request): the marker sits inside a value the request names, or inside a longer word.
_NOT_APPROVALS = [
    ("whole-word-in-double-quotes", 'delete my Apple note "{Marker} list"'),
    ("whole-word-in-curly-quotes", "delete my Apple note “{Marker} list”"),
    ("whole-word-in-single-quotes", "delete my apple note '{Marker} list'"),
    ("title-is-the-marker", 'delete my Apple note "{Marker}"'),
    ("substring-at-the-end-of-a-quoted-word", 'delete my Apple note "{Marker}s"'),
    ("substring-at-the-start-of-a-quoted-word", 'delete my Apple note "Un{marker}"'),
    ("whole-word-in-a-quoted-folder-name", 'delete my Apple note "Plan" in the "{Marker}" folder'),
    ("whole-word-in-a-folder-name", 'delete my Apple note "Plan" in the {Marker} folder'),
    ("whole-word-in-an-account-name", 'delete my Apple note "Plan" in the Work folder of the {Marker} account'),
    ("whole-word-in-an-unquoted-title", "delete my apple note titled {Marker} list"),
    ("substring-at-the-end-of-a-request-word", 'delete my Apple note "Plan" {marker}s'),
    ("substring-at-the-start-of-a-request-word", 'delete my Apple note "Plan" un{marker}'),
]


@pytest.mark.parametrize("marker", _MARKERS)
@pytest.mark.parametrize(("shape", "template"), _NOT_APPROVALS, ids=[row[0] for row in _NOT_APPROVALS])
def test_a_marker_inside_a_value_or_a_longer_word_is_not_an_approval(marker, shape, template):
    text = template.format(marker=marker, Marker=marker.capitalize())
    assert _intent(text, "apple_note_delete").approval_requested is False, (shape, text)


def test_the_reported_titles_are_not_approvals():
    for title in ("Proceeds 2026", "Do it later", "Eyes on Q3"):
        intent = _intent(f'delete my Apple note "{title}"', "apple_note_delete")
        assert (intent.target_label, intent.approval_requested) == (title, False), title


# (case, request): unrelated words and titles the repair never saw, each holding a marker inside a longer word or a value.
_NOVEL_NOT_APPROVALS = [
    ("yesterday", 'delete my Apple note "Groceries" from yesterday'),
    ("cargo-ahead", 'delete my Apple note "Cargo ahead of schedule"'),
    ("undo-itinerary", "remove my apple note “Undo itinerary edits”"),
    ("disapproved", "delete my apple note 'Disapproved expenses'"),
    ("apostrophe-inside-the-title", 'delete my Apple note "Yesterday\'s standup"'),
    ("allergies-unquoted", "delete Allergies apple note"),
    ("title-about", "delete my apple note about yes votes"),
    ("title-called-with-scope", 'delete the apple note called Go ahead plan in the Do it folder'),
]


@pytest.mark.parametrize(("case", "text"), _NOVEL_NOT_APPROVALS, ids=[row[0] for row in _NOVEL_NOT_APPROVALS])
def test_novel_words_holding_a_marker_are_not_approvals(case, text):
    assert _intent(text, "apple_note_delete").approval_requested is False, (case, text)


# (shape, request): the marker is one of the request's own words.
_APPROVALS = [
    ("leading-with-comma", '{marker}, delete my Apple note "Plan"'),
    ("leading-over-a-title-holding-it", '{marker} delete my Apple note "{Marker} list"'),
    ("trailing", 'delete my Apple note "Plan", {marker}'),
    ("shouted", '{MARKER} - DELETE MY APPLE NOTE "PLAN"'),
    ("beside-a-folder-holding-it", '{marker}: delete my Apple note "Plan" in the {Marker} folder'),
    ("beside-an-unquoted-title-holding-it", "{marker}, delete my apple note titled {Marker} list"),
]


@pytest.mark.parametrize("marker", _CONFIRMING)
@pytest.mark.parametrize(("shape", "template"), _APPROVALS, ids=[row[0] for row in _APPROVALS])
def test_a_marker_in_the_requests_own_words_is_an_approval(marker, shape, template):
    text = template.format(marker=marker, Marker=marker.capitalize(), MARKER=marker.upper())
    assert _intent(text, "apple_note_delete").approval_requested is True, (shape, text)


# (case, request): sloppy approvals typed by hand.
_SLOPPY_APPROVALS = [
    ("extra-spaces", 'go    ahead and delete my apple note "Plan"'),
    ("line-break", 'yes,\ndelete my apple note "Plan"'),
    ("no-comma", 'yes delete apple note "Eyes on Q3"'),
    ("filler", 'ok yes pls delete my apple note "Proceeds 2026" thx'),
    ("after-the-title", 'delete my apple note "Do it later" -- do it'),
    ("single-quoted-title", "proceed and delete my apple note 'Plan'"),
]


@pytest.mark.parametrize(("case", "text"), _SLOPPY_APPROVALS, ids=[row[0] for row in _SLOPPY_APPROVALS])
def test_sloppy_approvals_in_the_requests_own_words_still_approve(case, text):
    assert _intent(text, "apple_note_delete").approval_requested is True, (case, text)


@pytest.mark.parametrize(
    ("title", "scope"),
    [
        ("Plan", {"folder": "", "account": ""}),
        ("Eyes on Q3", {"folder": "", "account": ""}),
        ("Yes list", {"folder": "Go ahead", "account": ""}),
        ("Proceeds 2026", {"folder": "Work", "account": "Do it"}),
        ("Plan", {"folder": "Approve", "account": "iCloud"}),
        ("Plan", {"folder": "Yes", "account": "Proceed"}),
    ],
    ids=lambda value: value if isinstance(value, str) else f"{value['folder'] or '-'}|{value['account'] or '-'}",
)
def test_the_confirmation_a_reply_offers_approves_the_note_it_names(title, scope):
    """The delete confirmation repeats the title and scope; its own "yes" approves, the names it repeats do not."""
    from core.local_operator_actions import _apple_note_scope_words

    confirmation = f'yes, delete the apple note "{title}"{_apple_note_scope_words(scope)}'
    intent = _intent(confirmation, "apple_note_delete")
    assert (intent.target_label, intent.approval_requested) == (title, True), confirmation
    reread = parse_notes_destination(confirmation)
    assert (reread["folder"], reread["account"]) == (scope["folder"], scope["account"]), (confirmation, reread)
    unconfirmed = confirmation.replace("yes, ", "", 1)
    assert _intent(unconfirmed, "apple_note_delete").approval_requested is False, unconfirmed


# (case, request, kind, approved): the sibling kinds that read the same approval words.
_SIBLINGS = [
    ("schedule-quoted-title-substring", 'schedule a meeting "Proceeds review" on 2026-09-21 15:30 for 45m', "schedule_calendar_event", False),
    ("schedule-quoted-title-whole-word", 'schedule a meeting "Go ahead sync" on 2026-09-21 15:30 for 45m', "schedule_calendar_event", False),
    ("schedule-own-words", 'schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m, yes', "schedule_calendar_event", True),
    ("schedule-plain", 'schedule a meeting "Ops Sync" on 2026-09-21 15:30 for 45m', "schedule_calendar_event", False),
    ("propose-quoted-title", 'propose a meeting "Eyes on Q3" tomorrow at 10', "propose_calendar_event", False),
    ("propose-own-words", 'yes, propose a meeting "Ops Sync" tomorrow at 10', "propose_calendar_event", True),
    ("cleanup-quoted-folder", 'clean temp files in "Do it later"', "cleanup_temp_files", False),
    ("cleanup-word-holding-clean-all", "clean allotment temp files", "cleanup_temp_files", False),
    ("cleanup-own-words", "clean all temp files", "cleanup_temp_files", True),
    ("cleanup-own-words-with-filler", "fuck it, clean all temp files", "cleanup_temp_files", True),
    ("move-quoted-names-substring", 'move "Proceeds 2026.xlsx" to "Finance"', "move_path", False),
    ("move-quoted-names-whole-word", 'move "Yes votes.csv" to "Approve later"', "move_path", False),
    ("move-own-words", 'move "report.txt" to "archive", go ahead', "move_path", True),
]


@pytest.mark.parametrize(("case", "text", "kind", "approved"), _SIBLINGS, ids=[row[0] for row in _SIBLINGS])
def test_sibling_kinds_read_approval_from_the_requests_own_words(case, text, kind, approved):
    assert _intent(text, kind).approval_requested is approved, (case, text)


_ID = "1a2b3c4d-1111-2222-3333-444455556666"


def test_an_approval_id_selects_its_approval_kind_only_with_the_word_approve_itself():
    """An id beside "calendar", "meeting" or "move" selects that kind's approval only when the request says "approve".

    The kind alone runs a pending action when the autonomy gate asks for no approval (a calendar draft under hands-off, a
    move under Auto), so a question about the id must not select the kind at all, not merely arrive unapproved.
    """
    calendar = _intent(f"approve calendar {_ID}", "schedule_calendar_event")
    assert (calendar.action_id, calendar.approval_requested) == (_ID, True)
    moved = _intent(f"approve move {_ID}", "move_path")
    assert (moved.action_id, moved.approval_requested) == (_ID, True)
    for text in (f"is calendar {_ID} approved?", f"was move {_ID} approved?", f'calendar {_ID} "Approve budget"',
                 f"move {_ID} disapproved", f"has the meeting {_ID} been approved yet"):
        assert parse_operator_action_intent(text) is None, text


def test_paths_holding_a_marker_are_not_approvals():
    """A path names a place; its components were never words of the request (the paths are blanked before approval is read)."""
    intent = _intent('clean temp files in "/tmp/yes/go ahead"', "cleanup_temp_files")
    assert intent.approval_requested is False
    intent = _intent("yes, clean temp files in /tmp/yes", "cleanup_temp_files")
    assert intent.approval_requested is True
    intent = _intent("clean temp files in /tmp/yes", "cleanup_temp_files")
    assert intent.approval_requested is False


@pytest.mark.parametrize(
    "text", ["delete my Apple note \"Plan\", actually don't do it", 'do not proceed, delete my apple note "Plan" later'],
)
def test_a_negated_approval_word_does_not_approve(text):
    assert _intent(text, "apple_note_delete").approval_requested is False, text


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ('go "the" ahead: delete my apple note "Plan"', "apple_note_delete"),
        ("do 'Plan' it, delete my apple note 'Plan'", "apple_note_delete"),
        ('clean "the" all temp files', "cleanup_temp_files"),
    ],
)
def test_words_on_either_side_of_a_value_never_join_into_an_approval(text, kind):
    assert _intent(text, kind).approval_requested is False, text


# --------------------------------------------------------------------------- the proceed reading of a follow-up

def _proceed(text):
    from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin

    return type("Reader", (ProceedIntentSupportMixin,), {})()._is_proceed_message(text)


# (case, message): the proceed word sits inside a value the message names, so the message is its own request, never a
# resume of the turn before it (`core.agent_runtime.checkpoints.prepare_runtime_checkpoint`).
_NOT_PROCEEDS = [
    ("offered-confirmation-go-ahead-folder", 'yes, delete the apple note "Yes list" in the Go ahead folder'),
    ("rename-in-a-go-ahead-folder", 'rename my apple note "Plan" in the Go ahead folder to "Plan v2"'),
    ("read-in-a-continue-folder", 'open my Apple note "Plan" in the Continue folder'),
    ("listing-in-a-do-it-account", "show my Apple Notes in the Do it account"),
    ("quoted-with-inner-spaces", 'show my apple note " go ahead " please'),
    ("lowercase-carry-on-folder", 'append to my apple note "Trip" in the carry on folder with "passport"'),
    ("unquoted-titled", "delete my apple note titled Go ahead list"),
    ("unquoted-named", "remove apple note named Do it later"),
    ("unquoted-called-read", "open my apple note called Carry on"),
]


@pytest.mark.parametrize(("case", "text"), _NOT_PROCEEDS, ids=[row[0] for row in _NOT_PROCEEDS])
def test_a_proceed_word_inside_a_value_does_not_make_a_proceed_message(case, text):
    assert _proceed(text) is False, (case, text)


@pytest.mark.parametrize(
    "text",
    ["yes", "go ahead", "yes, go ahead", "continue", "ok do it", "go ahead with the Q3 plan", "carry on in the Work folder",
     'go ahead and delete my Apple note "Proceeds 2026"'],
)
def test_proceed_words_in_the_messages_own_words_still_proceed(text):
    assert _proceed(text) is True, text

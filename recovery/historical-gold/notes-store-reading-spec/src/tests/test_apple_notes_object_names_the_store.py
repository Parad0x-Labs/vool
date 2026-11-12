"""An explicit Apple Notes object names the Apple Notes store, for a read or find verb and for a create verb.

MEASURED AT 1d58a641 (`core.operator.parser.parse_operator_action_intent`, `core.operator.apple_notes.parse_notes_destination`):
* 'in the On My Mac account, find my Apple note "Plan"' and 'find my Apple note "Plan" in the On My Mac account' parsed as
  the workspace search `find_notes`; served, the reply was 'No notes in this workspace match ...';
* 'In my Travel folder, create an Apple note titled "Trip" with: pack chargers' parsed as `save_note`, and the destination
  reader said the request named no Apple Notes, so the note was written to the workspace, while '... create a note in
  Apple Notes titled "Trip" ...' was created in Apple Notes.

THE CLASS. The store a notes request addresses was decided by which verb list its verb was in and by the number of the
app words. The Apple read regex knew read/show/open/display with a singular "apple note"; the Apple list regex knew
list/show with a plural "apple notes"; the create destination knew only "Apple Notes" and "Notes.app". Each of them read
quoted titles and payloads as if the user had said them to the runtime. So find/search/look up/look for fell to the
workspace search, a singular "Apple note" fell to the workspace save, 'show my note "Plan" from Apple Notes' listed
instead of reading, and 'create a note titled "Apple Notes export"' went to Apple Notes.

THE READING UNDER TEST.
* The store: the request's own words name the Notes app -- "Apple note(s)", "Apple Notes", "Notes app", "Notes.app",
  or a quoted app name written as a place -- outside quoted values, outside the values a reader already took as data (a
  find query, an unquoted title, a create payload) and outside paths (`apple_notes.names_apple_notes`, built on the
  scope reader's own app words; "apple" takes the one typo budget of `core.typo_fold`).
* Every notes read of the parser asks it. A read verb reaches Apple Notes only when the verb takes the note it names: a
  title right after the verb, or the note noun reached without a phrase or clause of its own in between ('search the web
  for Apple Notes tips' reads no note).
* In Apple Notes, a read of one note (a title, or a singular note) is `apple_note_read`; a read of the notes (plural, no
  title) is `apple_note_list`, which carries a find topic so the reply can say it was not searched for.
* A create keeps its kind (`save_note`); its destination reader asks the same store reading.

In-process seam tests: no Notes bridge, no model, no socket. The served effects are in
tests/pa_beta_gate/test_served_apple_notes_object_reading.py.
"""
from __future__ import annotations

import pytest

from core.operator.apple_notes import parse_notes_destination
from core.operator.notes import parse_note_request
from core.operator.parser import parse_operator_action_intent

_APPLE_READS = frozenset({"apple_note_read", "apple_note_list"})

#: (case, wording, the title read): a read of ONE Apple note. The reported wordings first.
ONE_NOTE_READS = [
    ("original-account-first", 'in the On My Mac account, find my Apple note "Plan"', "Plan"),
    ("original-account-last", 'find my Apple note "Plan" in the On My Mac account', "Plan"),
    ("paraphrase-search-for-in-folder", 'search for my Apple note "Plan" in the Work folder', "Plan"),
    ("paraphrase-look-up-from-account", 'look up the Apple note "Receipts" from my Gmail account\'s Home folder', "Receipts"),
    ("paraphrase-polite-question-titled",
     'can you find the Apple note titled "Receipts" in the Taxes 2026 folder of my Gmail account', "Receipts"),
    ("paraphrase-scope-first-look-for-called", "in the Personal folder, look for my Apple note called Plan", "Plan"),
    ("paraphrase-store-as-a-place", 'find my note "Groceries" in Apple Notes', "Groceries"),
    ("paraphrase-notes-app-as-a-place", 'look for the note "Packing list" from the Travel folder in the Notes app', "Packing list"),
    ("paraphrase-title-before-the-notes", 'find "Packing list" among my Apple notes in the Local folder', "Packing list"),
    ("paraphrase-open-in-notes-dot-app", 'open the note "Groceries" in Notes.app', "Groceries"),
    ("paraphrase-show-from-the-app", 'show my note "Plan" from Apple Notes, Personal folder', "Plan"),
    ("sloppy-shouted", 'FIND MY APPLE NOTE "Plan" IN THE ON MY MAC ACCOUNT', "Plan"),
    ("sloppy-store-typo", 'find my aple note "Plan" in the On My Mac account', "Plan"),
    ("sloppy-no-determiners-lowercase", 'find apple note "Packing list" in travel folder', "Packing list"),
    ("sloppy-dropped-preposition", 'look up my apple note "Plan" personal folder', "Plan"),
    ("sloppy-casual-filler", 'yo can u find apple note "Receipts" in the Home folder thx', "Receipts"),
    ("sloppy-reordered-scope-before-object", 'find in the Personal folder my Apple note "Plan"', "Plan"),
    ("sloppy-transposed-store", 'search for my appel note "Receipts" in the Home folder', "Receipts"),
]

#: (case, wording, the find topic the listing carries): a read of the Apple NOTES, no title.
NOTES_LISTINGS = [
    ("listing-find-in-folder", "find my Apple notes in the Travel folder", None),
    ("listing-search-topic", "search my Apple Notes for packing", "packing"),
    ("listing-look-for-about", "look for Apple notes about groceries", "groceries"),
    ("listing-store-as-a-place", "find my notes in Apple Notes in the Gmail account", None),
    ("listing-sloppy", "find all my apple notes in teh local folder pls", None),
    ("listing-show-in-folder-unchanged", "show my Apple Notes in the Travel folder", None),
    ("listing-list-unchanged", "list my Apple notes", None),
]

#: (case, wording, folder, account): creates that name an Apple note. A payload after "with:" is data, so a scope inside it
#: addresses nothing (the scope reader's law, tests/test_notes_scope_reader.py).
CREATES = [
    ("original-scope-first", 'In my Travel folder, create an Apple note titled "Trip" with: pack chargers', "Travel", ""),
    ("original-scope-in-the-payload", 'create an Apple note titled "Trip" with: pack chargers in my Travel folder', "", ""),
    ("paraphrase-save-scope-before-payload", 'save an Apple note titled "Trip" in my Travel folder with: pack chargers', "Travel", ""),
    ("paraphrase-store-scope-before-title", 'store an Apple note in the Travel folder titled "Trip" with: pack chargers', "Travel", ""),
    ("paraphrase-jot-down-account", 'jot down an Apple note titled "Trip" in my iCloud account with: pack chargers', "", "iCloud"),
    ("paraphrase-new-note-folder-of-account",
     'create a new Apple note in the Travel folder of my iCloud account titled "Trip" with: pack chargers', "Travel", "iCloud"),
    ("paraphrase-notes-app-as-a-place",
     'save a note titled "Trip" to the Notes app in my Travel folder with: pack chargers', "Travel", ""),
    ("paraphrase-write-no-scope", 'write an Apple note titled "Trip" with: pack chargers', "", ""),
    ("sloppy-lowercase-no-articles", 'create apple note "Trip" in travel folder with: pack chargers', "travel", ""),
    ("sloppy-shouted", 'CREATE AN APPLE NOTE TITLED "Trip" IN MY TRAVEL FOLDER WITH: pack chargers', "TRAVEL", ""),
    ("sloppy-store-typo", 'create an aple note titled "Trip" in my Travel folder with: pack chargers', "Travel", ""),
    ("sloppy-filler-and-scope-typos",
     'pls create a apple note titled "Trip" in teh Travel fodler with: pack chargers thx', "Travel", ""),
    ("sloppy-dropped-preposition", 'create apple note "Trip" Travel folder with: pack chargers', "Travel", ""),
]

#: (case, wording, kind): workspace wordings. They name no Apple Notes store and keep their workspace kinds.
WORKSPACE_CONTROLS = [
    ("control-find-notes-about", "find my notes about packaging", "find_notes"),
    ("control-search-notes-for", "search notes for dial-in", "find_notes"),
    ("control-show-my-notes", "show my notes", "find_notes"),
    ("control-show-note-titled", 'show the note titled "Kickoff"', "show_note"),
    ("control-find-note-in-workspace", 'find my note "Plan" in the workspace', "find_notes"),
    ("control-save-note-titled", 'save a note titled "Kickoff" with: call the venue', "save_note"),
    ("control-create-note", 'create a note titled "Trip" with: pack chargers', "save_note"),
    ("control-save-note-field-title", "save a note with agenda: packaging and release checks", "save_note"),
]

#: (case, wording, kind): the app words sit inside a value the request carries -- a find topic, a quoted title, a payload,
#: a path -- or belong to another word. The request names no Apple Notes store.
NEAR_MISSES = [
    ("near-miss-app-words-in-the-topic", "find notes about the Apple note migration", "find_notes"),
    ("near-miss-app-name-in-the-topic", "find my notes about the Notes app setup", "find_notes"),
    ("near-miss-quoted-title-show", 'show my note titled "Apple Notes tips"', "show_note"),
    ("near-miss-quoted-title-read", 'read the note "Apple note ideas"', "show_note"),
    ("near-miss-quoted-create-title", 'create a note titled "Apple Notes export" with: steps', "save_note"),
    ("near-miss-payload-names-the-app", 'save a note titled "Groceries" with: export this to Apple Notes later', "save_note"),
    ("near-miss-payload-singular", 'save a note titled "Moving" with: find the Apple note about boxes', "save_note"),
    ("near-miss-quoted-singular-title", 'save a note titled "Apple note ideas" with: try widgets', "save_note"),
    ("near-miss-app-in-a-path", 'save a note about /Applications/Notes.app titled "Paths" with: check the bundle', "save_note"),
    ("near-miss-company-then-verb", "Apple noted record sales, find my notes about it", "find_notes"),
    ("adversarial-reminder", 'remind me to find my Apple note "Plan" tomorrow', "schedule_reminder"),
]

#: Sentences ABOUT Apple Notes whose read verb takes something else: a web page, a clause. They never read Apple Notes.
READS_OF_SOMETHING_ELSE = [
    ("adversarial-web-search", "search the web for Apple Notes tips"),
    ("adversarial-how-clause", "look up how Apple Notes syncs across devices"),
    ("adversarial-whether-clause", "find out whether Apple notes can be locked"),
    ("adversarial-online-search", "search online for Apple Notes templates"),
]


def _intent(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None, f"the operator parser read nothing in {text!r}"
    return intent


def _destination_store(text):
    body = parse_note_request(text)["body"]
    return parse_notes_destination(text, data=(body,))


@pytest.mark.parametrize(("case", "text", "title"), ONE_NOTE_READS, ids=[row[0] for row in ONE_NOTE_READS])
def test_a_read_of_one_named_apple_note_reads_it_in_apple_notes(case, text, title):
    intent = _intent(text)
    assert (intent.kind, intent.target_label) == ("apple_note_read", title), (case, intent)


@pytest.mark.parametrize(("case", "text", "topic"), NOTES_LISTINGS, ids=[row[0] for row in NOTES_LISTINGS])
def test_a_read_of_the_apple_notes_lists_them_and_carries_its_topic(case, text, topic):
    intent = _intent(text)
    assert (intent.kind, intent.target_label) == ("apple_note_list", topic), (case, intent)


@pytest.mark.parametrize(("case", "text", "folder", "account"), CREATES, ids=[row[0] for row in CREATES])
def test_a_create_that_names_an_apple_note_is_destined_for_apple_notes(case, text, folder, account):
    assert _intent(text).kind == "save_note", case
    destination = _destination_store(text)
    assert destination == {"wants_apple_notes": "True", "folder": folder, "account": account}, (case, destination)


@pytest.mark.parametrize(("case", "text", "kind"), WORKSPACE_CONTROLS + NEAR_MISSES,
                         ids=[row[0] for row in WORKSPACE_CONTROLS + NEAR_MISSES])
def test_a_request_that_names_no_apple_notes_store_keeps_its_kind(case, text, kind):
    intent = _intent(text)
    assert intent.kind == kind, (case, intent)
    if kind == "save_note":
        assert _destination_store(text)["wants_apple_notes"] == "False", (case, _destination_store(text))


@pytest.mark.parametrize(("case", "text"), READS_OF_SOMETHING_ELSE, ids=[row[0] for row in READS_OF_SOMETHING_ELSE])
def test_a_read_verb_that_takes_something_else_never_reads_apple_notes(case, text):
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind not in _APPLE_READS, (case, intent)


@pytest.mark.parametrize("text", ["how do I find an Apple note?", "find my Apple note in the Work folder"])
def test_one_apple_note_without_a_title_is_a_read_that_asks_which_and_lists_nothing(text):
    intent = _intent(text)
    assert (intent.kind, intent.target_label) == ("apple_note_read", None), (text, intent)


def test_a_quoted_app_name_names_the_store_only_where_it_is_the_place():
    assert _destination_store('save a note to "Apple Notes" titled "Trip" with: pack chargers')["wants_apple_notes"] == "True"
    assert _destination_store('save a note titled "Apple Notes" with: pack chargers')["wants_apple_notes"] == "False"


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("find my aple note", True),
        ("find my Appel notes", True),
        ("find my appple note", True),
        ("find my apple note", True),
        ("find my apples notes", False),
        ("find my applet notes", False),
        ("find my ample notes", False),
        ("Apple noted it, find my notes", False),
        ("find my notes", False),
        ("put it in the Notes app", True),
        ("put it in Notes.app", True),
    ],
)
def test_the_store_word_takes_one_typo_and_no_other_word(text, names):
    from core.operator.apple_notes import names_apple_notes

    assert names_apple_notes(text) is names, text


def test_the_scope_reader_and_the_store_reading_read_the_same_app_words():
    """A mistyped app name bounds a folder name exactly as the app name does, and names the store."""
    destination = parse_notes_destination("save a note to aple notes in my Work folder")
    assert destination == {"wants_apple_notes": "True", "folder": "Work", "account": ""}, destination

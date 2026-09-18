"""The Notes scope reader: which folder and account a request names, and which quoted values are data.

`core.operator.apple_notes.parse_notes_destination` read scope with two unanchored regexes until 2026-09-15. At
d6a398af: 'the Work folder of the iCloud account' gave the account "Work folder of the iCloud"; 'teh work folder' gave
the folder "teh work"; 'the Work fodler' and 'my iCloud acount' dropped the scope; 'the folder named Work' gave the
folder "the"; 'to Apple Notes in my Work folder' gave "Apple Notes in my Work"; a quoted payload ("put it in the Work
folder") was read as scope. The parser took a quoted folder name as the title or the appended text, and an apostrophe
("it's done") cut an appended payload short.

These are seam tests of the reader, the parser's Apple-note values and the handlers' scope words. The served effects
live in tests/pa_beta_gate/test_served_notes_scope_reading.py.
"""
from __future__ import annotations

import pytest

from core.operator.apple_notes import parse_notes_destination
from core.operator.parser import parse_operator_action_intent

# (case, request, folder, account)
_READINGS = [
    # the reported wordings
    ("folder-of-account", 'rename my Apple note "Plan" in the Work folder of the iCloud account to "Plan v2"', "Work", "iCloud"),
    ("typo-article", 'rename my apple note "Plan" in teh work folder to "Plan v2"', "work", ""),
    ("show-in-folder", 'show my Apple note "Plan" in the Work folder', "Work", ""),
    # phrase shapes
    ("possessive-account", "rename my Apple note \"Plan\" in my iCloud account's Work folder to \"Plan v2\"", "Work", "iCloud"),
    ("on-my-account", 'rename my Apple note "Plan" in the Work folder on my iCloud account to "Plan v2"', "Work", "iCloud"),
    ("from-under", 'rename my Apple note "Plan" from the Work folder under the iCloud account to "Plan v2"', "Work", "iCloud"),
    ("scope-first", 'in the iCloud account, rename the Apple note "Plan" in the Work folder to "Plan v2"', "Work", "iCloud"),
    ("folder-named", 'rename my Apple note "Plan" in the folder named Work of the iCloud account to "Plan v2"', "Work", "iCloud"),
    ("folder-called-quoted", 'show my Apple note "Plan" in the folder called "Team Q3"', "Team Q3", ""),
    ("relative-clause", 'read the Apple note "Plan" that is in my Work folder', "Work", ""),
    ("from-inside", 'show my apple note "Plan" from inside the Work folder', "Work", ""),
    ("name-with-preposition", 'rename my apple note "Plan" in the Letters from Home folder to "Plan v2"', "Letters from Home", ""),
    ("name-with-conjunction", 'show my apple note "Plan" in the Q3 and Q4 folder', "Q3 and Q4", ""),
    ("account-named-with-function-words", 'rename my Apple note "Plan" in the Work folder of the On My Mac account to "x"', "Work", "On My Mac"),
    ("account-named-with-function-words-bare", 'show my Apple note "Plan" in On My Mac account', "", "On My Mac"),
    ("create-after-the-app", "save a note to Apple Notes in my Work folder", "Work", ""),
    ("create-folder-of-account", 'save a note to Apple Notes in the Work folder of the iCloud account titled "Trip" with: pack', "Work", "iCloud"),
    ("create-notes-app", "save a note to the Notes app in my Work folder", "Work", ""),
    # quoted names
    ("quoted-names", 'rename my Apple note "Plan" in the "Work" folder of the "iCloud" account to "Plan v2"', "Work", "iCloud"),
    ("curly-quoted-name", "show my Apple note “Plan” in the “Work” folder", "Work", ""),
    ("single-quoted-name", "show my apple note 'Plan' in the 'Work' folder", "Work", ""),
    ("quoted-name-with-punctuation", 'show my apple note "Plan" in the "Team: Q3" folder', "Team: Q3", ""),
    # sloppy
    ("no-determiners", 'rename apple note "Plan" in work folder of icloud account to "Plan v2"', "work", "icloud"),
    ("shouted", 'RENAME MY APPLE NOTE "Plan" IN TEH WORK FOLDER OF THE ICLOUD ACCOUNT TO "Plan v2"', "WORK", "ICLOUD"),
    ("noun-typos", 'pls rename my apple note "Plan" in the Work fodler of the iCloud acount to "Plan v2" thx', "Work", "iCloud"),
    ("transposed-article", 'rename my apple note "Plan" in hte work folder to "Plan v2"', "work", ""),
    ("preposition-and-article-typos", 'rename my apple note "Plan" form teh work folder to "Plan v2"', "work", ""),
    ("possessive-typo", 'rename my apple note "Plan" in yuor work folder to "Plan v2"', "work", ""),
    ("dropped-preposition", 'rename my apple note "Plan" teh work folder to "Plan v2"!!', "work", ""),
    ("parenthetical-abbreviation", 'can u rename my apple note "Plan" (Work folder, iCloud acct) to "Plan v2"?', "Work", "iCloud"),
    ("plural-noun", 'show my apple note "Plan" in the Work folders', "Work", ""),
    ("trailing-filler", 'open apple note "Plan" in teh work fodler pls', "work", ""),
    # names are never folded; words that only look like the reader's vocabulary stay names
    ("name-typo-kept", 'rename my apple note "Plan" in the wrok folder to "Plan v2"', "wrok", ""),
    ("capitalised-near-miss-of-the", 'rename my apple note "Plan" in Theo folder to "Plan v2"', "Theo", ""),
    ("capitalised-near-miss-starts-a-longer-name", 'show my apple note "Plan" in Theo Studio folder', "Theo Studio", ""),
    ("lowercase-near-miss-alone", 'show my apple note "Plan" in theo folder', "theo", ""),
    ("capitalised-near-miss-of-from", 'show my apple note "Plan" in the Tax Form folder', "Tax Form", ""),
    ("noun-shaped-name", 'show my apple note "Plan" in the Accounts folder', "Accounts", ""),
    ("noun-near-miss-inside-name", 'show my apple note "Plan" in the older notes folder', "older notes", ""),
    ("noun-shaped-name-after-naming-word", 'show my apple note "Plan" in the folder named Accounts', "Accounts", ""),
    ("apple-notes-is-never-a-name", 'save a note to Apple Notes in the "Apple Notes" folder', "Apple Notes", ""),
    # no scope named
    ("no-scope", 'show my Apple note "Plan"', "", ""),
    ("title-reads-like-scope", 'show my Apple note "Work folder of the iCloud account"', "", ""),
    ("title-with-scope-words", 'append to my Apple note "Notes to self folder" with "x"', "", ""),
    ("payload-reads-like-scope", 'append to my Apple note "Ideas" with "put it in the Work folder"', "", ""),
    ("payload-with-typos", 'append to my apple note "Groceries" with "put teh eggs in teh work folder"', "", ""),
    ("unquoted-title-noun", "read my apple note called Account setup", "", ""),
    ("trailing-near-miss-noun", 'append to my apple note "Plan" with "x" for older users', "", ""),
    ("bare-folder-then-filler", 'delete my apple note "Plan" from the folder please', "", ""),
    ("deictic-folder", 'show my apple note "Plan" in this folder', "", ""),
    ("unrelated-folder-noun-in-clause", 'append to my apple note "Tasks" with clean up the downloads folder', "", ""),
]


@pytest.mark.parametrize(("case", "text", "folder", "account"), _READINGS, ids=[row[0] for row in _READINGS])
def test_the_reader_reads_the_folder_and_account_the_request_names(case, text, folder, account):
    destination = parse_notes_destination(text)
    assert (destination["folder"], destination["account"]) == (folder, account), (case, destination)


def test_values_the_caller_read_as_data_are_never_scope():
    """An unquoted payload or new name is data the handler already read; a scope phrase inside it names nothing."""
    appended = 'append to my apple note "Tasks" with move the scans into the downloads folder of my iCloud account'
    payload = "move the scans into the downloads folder of my iCloud account"
    assert parse_notes_destination(appended)["folder"] == "downloads", "unmasked, the phrase does read as scope"
    masked = parse_notes_destination(appended, data=(payload,))
    assert (masked["folder"], masked["account"]) == ("", ""), masked

    saved = 'save a note to Apple Notes in the Receipts folder titled "Moving" with: put the boxes into the garage folder'
    body = "put the boxes into the garage folder"
    assert parse_notes_destination(saved, data=(body,))["folder"] == "Receipts"


# (case, request, the quoted values that are data)
_DATA_VALUES = [
    ("quoted-folder-is-not-the-new-name", 'rename my Apple note "Plan" in the "Work" folder to "Plan v2"', ["Plan", "Plan v2"]),
    ("leading-quoted-scope-is-not-the-title", 'in the "Work" folder, show my Apple note "Plan"', ["Plan"]),
    ("apostrophe-inside-payload", "append to my Apple note \"Ideas\" with \"it's done\"", ["Ideas", "it's done"]),
    ("possessive-scope-before-payload", "append to my Apple note \"Plan\" in my iCloud account's Work folder with \"reviewed budget\"",
     ["Plan", "reviewed budget"]),
    ("single-quoted-values", "rename my apple note 'Plan' to 'Plan v2'", ["Plan", "Plan v2"]),
    ("title-that-reads-like-scope", 'show my Apple note "Work folder"', ["Work folder"]),
    ("post-nominal-quoted-scope", 'append to my apple note "Plan" in the folder named "Ops" with "done"', ["Plan", "done"]),
]


@pytest.mark.parametrize(("case", "text", "values"), _DATA_VALUES, ids=[row[0] for row in _DATA_VALUES])
def test_quoted_values_are_data_unless_they_name_a_scope(case, text, values):
    from core.operator.apple_notes import quoted_note_values

    assert quoted_note_values(text) == values, case


# (case, request, kind, title, payload): the parser's Apple-note values
_INTENTS = [
    ("append-quoted-folder", 'append to my Apple note "Plan" in the "Work" folder with "reviewed budget"',
     "apple_note_append", "Plan", "reviewed budget"),
    ("append-apostrophe", "append to my Apple note \"Ideas\" with \"it's done\"", "apple_note_append", "Ideas", "it's done"),
    ("append-possessive-scope", "append to my Apple note \"Plan\" in my iCloud account's Work folder with \"reviewed budget\"",
     "apple_note_append", "Plan", "reviewed budget"),
    ("read-leading-quoted-scope", 'in the "Work" folder, show my Apple note "Plan"', "apple_note_read", "Plan", None),
    ("delete-quoted-folder", 'delete my Apple note "Plan" from the "Work" folder', "apple_note_delete", "Plan", None),
    ("append-unquoted-payload", 'append to my apple note "Groceries" with move the eggs into the work folder',
     "apple_note_append", "Groceries", "move the eggs into the work folder"),
]


@pytest.mark.parametrize(("case", "text", "kind", "title", "payload"), _INTENTS, ids=[row[0] for row in _INTENTS])
def test_the_parser_takes_titles_and_payloads_from_data_values(case, text, kind, title, payload):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind, (case, intent)
    assert intent.target_label == title, (case, intent.target_label)
    if kind == "apple_note_append":
        assert intent.destination_path == payload, (case, intent.destination_path)


@pytest.mark.parametrize(
    "scope",
    [
        {"folder": "Work", "account": "iCloud"},
        {"folder": "Letters from Home", "account": "On My Mac"},
        {"folder": "Apple Notes", "account": ""},
        {"folder": "Team: Q3", "account": ""},
        {"folder": "", "account": "Accounts folder"},
        {"folder": "Taxes 2026", "account": "Gmail"},
    ],
    ids=lambda scope: f"{scope['folder'] or '-'}|{scope['account'] or '-'}",
)
def test_the_scope_words_a_reply_offers_read_back_to_the_same_scope(scope):
    """The delete confirmation quotes the scope back; typing it must name the same folder and account."""
    from core.local_operator_actions import _apple_note_scope_words

    words = _apple_note_scope_words(scope)
    confirmation = f'yes, delete the apple note "Plan"{words}'
    reread = parse_notes_destination(confirmation)
    assert (reread["folder"], reread["account"]) == (scope["folder"], scope["account"]), (confirmation, reread)
    assert parse_operator_action_intent(confirmation).target_label == "Plan", confirmation

"""keep-a-note-20261028 — "keep a note" lands where "show my notes" looks.

WHAT THIS FILE PINS
-------------------
The organizer finding (N02/N06): "keep a note ..." fell through to memory capture, so the
later "show my notes" — which reads the NOTES store — made the note look lost; and the
saved reply showed the note's file path.

* PARSER: a note-keeping ask (keep/add/put down/stick/save-this, with "note" as the
  object, asked at the sentence start or after please / can you / could you / will you /
  would you) parses as `save_note`. A subject before the verb, a question word, a definite
  the/this/that note, plural notes without an ask, or a note addressed to a person does
  not. The AMENDMENT 1 acceptance rows run here verbatim, plus the milestone-1 negatives
  and fresh wordings of this file's own.
* SERVED TURN: through `VoolAgent.run_once` — the same entry point the daemon calls for a
  turn — a note ask writes a real `*.md` file under the workspace `notes/` directory, and
  the next turn in the SAME session lists it back. The notes-store file plus the listing
  is the whole proof of order here: whether a memory lane also recorded anything is
  another team's question and is deliberately not read or asserted by this file.
* REPLY: the saved reply says "Saved your note '<title>'." and carries no machine path —
  no filesystem separator, no file suffix, no workspace-root fragment.
* AMENDMENT 2: a note added ONTO another thing (an invoice, an email, a piano, a door, a
  card) is never a save — only the notes store itself, "for me"/"for later" or nothing may
  sit between the note object and its words — and "of" carries the words only straight
  after the note. The listing replies' note lines are exactly "- <title>" (bare) /
  "- <title>: <excerpt>" (match), "(linked to an event)" in words when the link is shown,
  never a path, a file suffix or an event id; the paths stay in the result details.
"""
from __future__ import annotations

import uuid

import pytest

from core.operator.parser import parse_operator_action_intent
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

# ------------------------------------------------------------------ 1. the parser rows

# The mandated acceptance set, verbatim (TASK.md AMENDMENT 1).
_AMENDMENT_MUST_SAVE = (
    "please keep a note that the plumber comes at 9 on Monday",
    "keep a note: the calendar is full on Monday",
    "keep a note for me: garage keypad code is 5523",
    "add a note to my notes app that the boiler service is due nov 12",
    "save this one: buy zucchini and halloumi for saturdays bbq",
)
_AMENDMENT_MUST_NOT = (
    "I keep a note that my insurance renews in May",
    "why should I keep a note that nobody reads?",
    "my therapist told me to keep a note that tracks my mood",
    "she said to keep a note that the door sticks",
    "can you keep the note short",
    "keep this note private",
    "keep that note, I like it",
    "I always keep a note on the fridge for my son",
    "should I keep a note that I paid him?",
)
# The milestone-1 negatives (TASK.md, Milestones): "keep in mind", a "note that ..."
# said as information, a note that IS a physical object, and a question about kept notes.
_MILESTONE_MUST_NOT = (
    "keep in mind that the bins go out on Tuesday morning",
    "note that the shop closes at 6",
    "a note on the piano",
    "what notes did I keep",
)
# Fresh asks, different in surface form from the AMENDMENT rows: the verbs keep / add /
# put down / stick / save-this at the sentence start or after please / can you / could
# you / would you, with the content as a that-clause, a colon, "for me:" or "of ...".
_FRESH_MUST_SAVE = (
    "keep a note that the piano tuner charges forty euros an hour",
    "please add a note that the car service is booked for March 3",
    "can you put down a note for me: dentist at 11 on Thursday",
    "could you stick a note in my notes that the ladder is in the shed",
    "would you keep a note of the dog walker's phone number",
    "stick a note in my notes: the spare key is under the third plant pot",
    "add a note for me: the pharmacy opens at half eight on Saturdays",
    "save this: the neighbour's cat is called Miso",
    "keep a quick note that the bike lock combination is 7291",
    "would you put down a note that the recycling goes out on Friday",
    "would you keep a note of the electric meter reading 4471",
    "can you add a note: the rent now goes out on the first",
)
# Fresh negatives: a subject before the verb (we / she / I / the kids, present and past),
# question words (how / where), a definite the/this/that note, plural notes in a habitual
# statement, and a note whose words are addressed to a person.
_FRESH_MUST_NOT = (
    "we keep a note of every appliance purchase in this house",
    "how do I keep a note of what I spend on coffee?",
    "put down the note you drafted yesterday, that version was clearer",
    "she sticks a note on the fridge every morning before work",
    "where do you keep the notes for the car?",
    "I put down a note of the serial number last time",
    "the kids add notes to the shopping list all day",
    "could you write a note for the teacher about the missed test",
)

# The mandated AMENDMENT 2 set, verbatim (TASK.md ## AMENDMENT 2): a note added onto another
# thing (an invoice, an email, a piano) is not a notes-store save, and "of" carries the note's
# words only straight after the note object ("keep a note of my blood pressure: 128 over 82").
# "add a note to my notes app that the boiler service is due nov 12" — the stays-a-save row of
# the same amendment — is already in _AMENDMENT_MUST_SAVE above.
_AMENDMENT2_MUST_SAVE = (
    "keep a note of my blood pressure: 128 over 82",
)
_AMENDMENT2_MUST_NOT = (
    "add a note to the invoice that payment is due in 30 days",
    "add a note to the email to Jim that I'll be late",
    "put a note on the piano so people know it is out of tune",
)
# AMENDMENT 2 fresh saves: the words may wait behind the notes store itself (a notebook,
# Apple Notes, the workspace), behind "for me"/"for later"/"to myself", or sit in "of"
# straight after the note. "on the calendar" is policy this file deliberately does not assert.
_PLACE_MUST_SAVE = (
    "put a note in my notebook that the bus stop moved to the north gate",
    "add a note to Apple Notes that the pharmacy closes at eight on Saturdays",
    "keep a note for later: ask the vet about the booster jab",
    "would you keep a note of the boiler serial number B4471QX",
    "could you add a note that the swim class starts at half seven",
    "keep a note to myself: the spare car key is with the neighbour",
    "keep a note in my workspace that the loft hatch sticks",
)
# AMENDMENT 2 fresh negatives: any OTHER thing between the note and its words — a whiteboard,
# a front door, a contract, a recipe card, a report, under a fridge magnet — keeps it out,
# with a that-clause or "saying" as the cue it hides behind; and "of" only ever carries the
# words straight after the note, never later in the sentence.
_PLACE_MUST_NOT = (
    "add a note to the whiteboard that the printer jams on double-sided",
    "put a note on the front door saying the delivery can wait",
    "add a note in the contract that the deposit is refundable",
    "keep a note on the recipe card that the oven runs hot",
    "put a note in the report that the figures are provisional",
    "stick a note under the fridge magnet that the takeaway menu is in the drawer",
    "keep a note so I don't lose track of the wifi code",
    "add a note to the card that says happy birthday",
)


def _ids(prefix: str, rows: tuple[str, ...]) -> list[str]:
    return [f"{prefix}{index + 1}" for index in range(len(rows))]


@pytest.mark.parametrize(
    "text",
    _AMENDMENT_MUST_SAVE + _AMENDMENT2_MUST_SAVE + _FRESH_MUST_SAVE + _PLACE_MUST_SAVE,
    ids=(
        _ids("amend-save-", _AMENDMENT_MUST_SAVE)
        + _ids("amend2-save-", _AMENDMENT2_MUST_SAVE)
        + _ids("fresh-save-", _FRESH_MUST_SAVE)
        + _ids("place-save-", _PLACE_MUST_SAVE)
    ),
)
def test_a_note_keeping_ask_parses_as_save_note(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None, "a note-keeping ask parsed to no operator action at all"
    assert intent.kind == "save_note", (
        f"a note-keeping ask parsed as {intent.kind!r} instead of the notes lane"
    )


@pytest.mark.parametrize(
    "text",
    _AMENDMENT_MUST_NOT
    + _AMENDMENT2_MUST_NOT
    + _MILESTONE_MUST_NOT
    + _FRESH_MUST_NOT
    + _PLACE_MUST_NOT,
    ids=(
        _ids("amend-not-", _AMENDMENT_MUST_NOT)
        + _ids("amend2-not-", _AMENDMENT2_MUST_NOT)
        + _ids("milestone-not-", _MILESTONE_MUST_NOT)
        + _ids("fresh-not-", _FRESH_MUST_NOT)
        + _ids("place-not-", _PLACE_MUST_NOT)
    ),
)
def test_a_statement_or_question_about_notes_never_parses_as_a_note_save(text):
    intent = parse_operator_action_intent(text)
    assert not (intent and intent.kind == "save_note"), (
        "words that talk about notes instead of asking to keep one were claimed as a "
        f"note save (parsed as {intent.kind if intent else None!r})"
    )


# ------------------------------------------------------------------ 2. the served two-turn flow


@pytest.fixture
def notes_env(tmp_path, monkeypatch):
    """One real agent turn against a disposable workspace — the served harness of
    tests/pa_beta_gate/test_served_calendar_notes_workflows.py, without the CalDAV
    fixture: notes only."""
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone("Europe/Athens")

    harness = _Harness(f"sess-note-{uuid.uuid4().hex[:8]}")
    yield {"harness": harness, "workspace": workspace}
    harness.close()


def _turn(harness: _Harness, text: str, workspace) -> str:
    """One REAL served turn, the same call the daemon makes. No model patch: an
    operator-intent turn is claimed by the operator lane before the model lane."""
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = str(workspace)
    context["operating_mode"] = "auto"
    result = harness.agent.run_once(
        text, source_context=context, session_id_override=harness.session_id
    )
    return str(result.get("response") or "")


def _note_files(workspace) -> list:
    notes_dir = workspace / "notes"
    return list(notes_dir.glob("*.md")) if notes_dir.is_dir() else []


def _front_matter_title(text: str) -> str:
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "title":
            return value.strip()
    return ""


def test_keep_a_note_ask_lands_in_the_notes_store_and_lists_next_turn(notes_env):
    """THE user-visible defect: the ask saves in the notes store (a real file under the
    workspace notes directory), and the next turn in the SAME session lists it back."""
    harness = notes_env["harness"]
    workspace = notes_env["workspace"]

    reply = _turn(harness, "keep a note for me: garage keypad code is 5523", workspace)
    assert "Saved your note" in reply, (
        f"the note-keeping ask was not answered as a saved note: {reply!r}"
    )
    # The saved reply carries no filesystem path: its only content is the note's own
    # words ("Saved your note '<title>'."), which contain no separator here.
    assert "/" not in reply, f"the saved reply carries a machine path: {reply!r}"

    files = _note_files(workspace)
    assert files, f"no note file was written under the workspace notes directory; reply was {reply!r}"
    saved = [path for path in files if "5523" in path.read_text(encoding="utf-8")]
    assert saved, "the ask's content never reached the notes store"

    listing = _turn(harness, "show my notes", workspace)
    title = _front_matter_title(saved[0].read_text(encoding="utf-8"))
    assert "5523" in listing or title in listing, (
        f"'show my notes' in the same session did not list the saved note: {listing!r}"
    )


def test_second_wording_lands_in_the_notes_store_the_same_way(notes_env):
    """A different ask shape — cue word plus a that-clause — saves and lists the same."""
    harness = notes_env["harness"]
    workspace = notes_env["workspace"]

    reply = _turn(
        harness, "please keep a note that the plumber comes at 9 on Monday", workspace
    )
    assert "Saved your note" in reply, f"the ask was not answered as a saved note: {reply!r}"
    assert "/" not in reply, f"the saved reply carries a machine path: {reply!r}"

    files = _note_files(workspace)
    assert files, f"no note file was written under the workspace notes directory; reply was {reply!r}"
    saved = [path for path in files if "plumber" in path.read_text(encoding="utf-8")]
    assert saved, "the ask's content never reached the notes store"

    listing = _turn(harness, "show my notes", workspace)
    title = _front_matter_title(saved[0].read_text(encoding="utf-8"))
    assert "plumber" in listing or title in listing, (
        f"'show my notes' in the same session did not list the saved note: {listing!r}"
    )


# ------------------------------------------------------------------ 3. the saved reply's shape


def test_the_saved_reply_names_the_note_without_any_machine_path(notes_env):
    """The saved reply begins with the words the person needs ("Saved your note ...") and
    carries no path fragment of any kind — no separator, no file suffix, no workspace-root
    substring."""
    harness = notes_env["harness"]
    workspace = notes_env["workspace"]

    reply = _turn(harness, "could you keep a note for me: the bike shed code is 8891", workspace)
    assert reply.startswith("Saved your note"), f"the saved reply's shape changed: {reply!r}"
    assert "/" not in reply, f"the saved reply carries a path separator: {reply!r}"
    assert ".md" not in reply, f"the saved reply names a file suffix: {reply!r}"
    assert str(workspace) not in reply, f"the saved reply carries the workspace root: {reply!r}"

    assert any("8891" in path.read_text(encoding="utf-8") for path in _note_files(workspace)), (
        "the note itself was still written where it belongs"
    )


# ------------------------------------------------- 4. the listing replies' shape (AMENDMENT 2, Fix 3)


def _turn_result(harness: _Harness, text: str, workspace) -> dict:
    """One REAL served turn, returning the whole result dict — the reply AND the details
    the runtime keeps for itself (where the note's path still lives)."""
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = str(workspace)
    context["operating_mode"] = "auto"
    return harness.agent.run_once(
        text, source_context=context, session_id_override=harness.session_id
    )


def _front_matter_body(text: str) -> str:
    """The note's words: everything after the closing '---' fence of the front matter."""
    lines = text.splitlines()
    fence = 0
    for index, line in enumerate(lines):
        if line.strip() == "---":
            fence += 1
            if fence == 2:
                return "\n".join(lines[index + 1 :]).strip()
    return text.strip()


def _saved_note(workspace, needle: str):
    """The one note file whose body carries `needle`, with its title and excerpt."""
    saved = [path for path in _note_files(workspace) if needle in path.read_text(encoding="utf-8")]
    assert saved, f"no note carrying {needle!r} was written under the workspace notes directory"
    text = saved[0].read_text(encoding="utf-8")
    return saved[0], _front_matter_title(text), _front_matter_body(text)[:200]


def _listing_lines(reply: str) -> list[str]:
    return [line for line in reply.splitlines() if line.startswith("- ")]


def test_the_notes_listing_lines_are_titles_only_never_paths(notes_env):
    """The listing reply's note lines are exactly "- <title>" (bare listing) and
    "- <title>: <excerpt>" (a match listing): no path separator, no file suffix, and the
    note's path still lives in the result details the runtime keeps for itself."""
    harness = notes_env["harness"]
    workspace = notes_env["workspace"]
    saved = _turn(harness, "keep a note for me: the pollen count is high today", workspace)
    assert "Saved your note" in saved, f"the ask was not answered as a saved note: {saved!r}"

    path, title, excerpt = _saved_note(workspace, "pollen")

    listing = _turn_result(harness, "show my notes", workspace)
    reply = str(listing.get("response") or "")
    lines = _listing_lines(reply)
    assert lines == [f"- {title}"], (
        f"the bare listing's note lines are not exactly '- <title>': {lines!r} in {reply!r}"
    )
    assert "/" not in reply, f"the bare listing carries a path separator: {reply!r}"
    assert ".md" not in reply, f"the bare listing names a file suffix: {reply!r}"

    match = _turn_result(harness, "find my notes about pollen", workspace)
    matched = str(match.get("response") or "")
    lines = _listing_lines(matched)
    assert lines == [f"- {title}: {excerpt}"], (
        f"the match listing's note lines are not exactly '- <title>: <excerpt>': {lines!r} in {matched!r}"
    )
    assert "/" not in matched, f"the match listing carries a path separator: {matched!r}"
    assert ".md" not in matched, f"the match listing names a file suffix: {matched!r}"

    for result in (listing, match):
        rows = result.get("details", {}).get("notes") or []
        assert rows and rows[0].get("note_path") == str(path), (
            "the note's path left the result details: the listing must read it, not lose it"
        )


def test_a_linked_note_lists_in_plain_words_without_its_event_id(notes_env):
    """A note linked to an event lists as "- <title> (linked to an event)" in words: the
    event id itself never reaches the reply — no uid fragment, no "(event " bracket, no
    "linked event" label, no path — while the path stays in the result details."""
    from core.operator.notes import link_note_to_event

    harness = notes_env["harness"]
    workspace = notes_env["workspace"]
    saved = _turn(harness, "keep a note for me: the chimney sweep is booked for the 5th", workspace)
    assert "Saved your note" in saved, f"the ask was not answered as a saved note: {saved!r}"

    path, title, excerpt = _saved_note(workspace, "chimney")
    event_uid = "cal-7f3ac9ab12de40f1"
    assert link_note_to_event(str(path), event_uid=event_uid), (
        "the note could not be linked to the event (link_note_to_event returned False)"
    )

    listing = _turn_result(harness, "show my notes", workspace)
    reply = str(listing.get("response") or "")
    lines = _listing_lines(reply)
    assert lines == [f"- {title} (linked to an event)"], (
        f"the linked bare listing is not '- <title> (linked to an event)': {lines!r} in {reply!r}"
    )

    match = _turn_result(harness, "find my notes about chimney", workspace)
    matched = str(match.get("response") or "")
    lines = _listing_lines(matched)
    assert lines == [f"- {title}: {excerpt} (linked to an event)"], (
        f"the linked match listing is not '- <title>: <excerpt> (linked to an event)': {lines!r} in {matched!r}"
    )

    for text in (reply, matched):
        assert "7f3ac9ab12de40f1" not in text, f"the listing leaks the event id: {text!r}"
        assert "(event " not in text, f"the listing brackets the event id: {text!r}"
        assert "linked event" not in text, f"the listing labels the event link: {text!r}"
        assert "/" not in text, f"the listing carries a path separator: {text!r}"
        assert ".md" not in text, f"the listing names a file suffix: {text!r}"
        assert "(linked to an event)" in text, f"the link left the listing's words: {text!r}"

    for result in (listing, match):
        rows = result.get("details", {}).get("notes") or []
        assert rows and rows[0].get("note_path") == str(path), (
            "the linked note's path left the result details: the listing must read it, not lose it"
        )

"""A Notes scope that sits between two requests reaches the Notes owner with its request, through the served turn.

Measured 2026-09-15 at 1d58a641 on the served path (`VoolAgent.run_once`, model stand-in, synthetic Notes runner):
'check the gold price, and in the Personal folder, open my Apple note "Plan"' and 'What is the gold price? In the
Personal folder, open my Apple note "Plan".' both ended as `deterministic:demand_owned_mixed_turn` with 'I could not
answer this part of your message: - open my Apple note "Plan" (could not be completed)'. The execution grain had carried
the folder into the gold request, and three notes are titled "Plan". The grain law is pinned in
tests/test_a_fragment_between_requests_rides_the_request_it_opens.py.

Every case is a real served turn in a fresh session (the cross-turn case keeps one session). Assertions read the
environment: the scripts the Notes bridge received (which note id was read), the synthetic store, and the route.
LABELLED: model stand-in, synthetic Notes runner (`FakeNotes`, never osascript), loopback CalDAV fixture from `served_env`.
"""
from __future__ import annotations

import pytest

from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env  # noqa: F401 -- fixture
from tests.pa_beta_gate.test_served_notes_scope_reading import (  # noqa: F401 -- fixture
    _ACCOUNT_SEED,
    _FOLDER_SEED,
    _NOVEL_SEED,
    ERRANDS_GROCERIES,
    GMAIL_TAXES,
    HOME_PLAN,
    TRAVEL_PACK,
    WORK_MAC,
    WORK_PLAN,
    _answer,
    _assert_nothing_written,
    _served,
    _session,
    served_notes,
)

pytestmark = [pytest.mark.pa_beta]

_MIXED_ROUTE = "deterministic:demand_owned_mixed_turn"
_SEEDS = {"folder": _FOLDER_SEED, "account": _ACCOUNT_SEED, "novel": _NOVEL_SEED}


def _read_ids(notes):
    """The note ids the Notes owner read a body from."""
    ids = []
    for script in notes.scripts:
        if "return body of theNote" in script and 'whose id is "' in script:
            ids.append(script.split('whose id is "')[1].split('"')[0])
    return ids


def _assert_read_only(result, notes, seed, note_ids, case):
    route = str(result.get("route") or "")
    answer = _answer(result)
    assert route == _MIXED_ROUTE, f"{case}: route={route!r} answer={answer[:300]!r}"
    assert sorted(set(_read_ids(notes))) == sorted(note_ids), f"{case}: read {_read_ids(notes)}, answer={answer[:300]!r}"
    for note_id in note_ids:
        assert seed[note_id]["body"] in answer, f"{case}: the named note's body is not shown: {answer[:400]!r}"
    others = [note["body"] for note_id, note in seed.items() if note_id not in note_ids and note["body"] in answer]
    assert others == [], f"{case}: another note's body was shown: {others}"
    assert "could not be completed" not in answer, f"{case}: {answer[:400]!r}"
    _assert_nothing_written(notes, seed, case)


# (case, seed, text, the note ids the Notes owner must read). Every title here is held by more than one note in its seed
# (except "Groceries", read beside a scoped "Plan"), so a scope left with the other request refuses or reads another note.
_BETWEEN_FAMILY = [
    ("original-coordinated", "folder", 'check the gold price, and in the Personal folder, open my Apple note "Plan"', [HOME_PLAN]),
    ("original-next-sentence", "folder", 'What is the gold price? In the Personal folder, open my Apple note "Plan".', [HOME_PLAN]),
    ("paraphrase-weather-inside-travel", "folder",
     'what is the weather in Lisbon, and inside the Travel folder, show my Apple note "Packing list"', [TRAVEL_PACK]),
    ("paraphrase-prose-from-work", "folder",
     'explain how tides work in two sentences, and from my Work folder, open the Apple note "Plan"', [WORK_PLAN]),
    ("paraphrase-conversion-then-folder-of-account", "account",
     'Convert 40 GBP to JPY. From the Work folder of the On My Mac account, display my Apple note "Plan".', [WORK_MAC]),
    ("paraphrase-clock-account-then-folder", "novel",
     'what time is it in Nairobi? In the Gmail account, in the Taxes 2026 folder, open my Apple note "Receipts".', [GMAIL_TAXES]),
    ("paraphrase-second-notes-request", "folder",
     'open my Apple note "Groceries", and in the Personal folder, open my Apple note "Plan"', [ERRANDS_GROCERIES, HOME_PLAN]),
    ("sloppy-typos", "folder", 'check teh gold price, and in teh personal folder, open my apple note "Plan"', [HOME_PLAN]),
    ("sloppy-no-space-after-question", "folder", 'what is the gold price?in the personal folder, open my apple note "Plan"', [HOME_PLAN]),
    ("sloppy-no-comma-before-and", "folder",
     'check the gold price and in the Travel folder, show my Apple note "Packing list"', [TRAVEL_PACK]),
    ("sloppy-casual-filler", "folder", 'gold price pls, and in the personal folder, show me my apple note "Plan" thx', [HOME_PLAN]),
    ("sloppy-dropped-preposition", "folder", 'check the gold price, and Work folder, open my Apple note "Plan"', [WORK_PLAN]),
    ("sloppy-shouted", "folder", 'CHECK THE GOLD PRICE, AND IN THE WORK FOLDER, OPEN MY APPLE NOTE "Plan"', [WORK_PLAN]),
]


@pytest.mark.parametrize(("case", "seed", "text", "note_ids"), _BETWEEN_FAMILY, ids=[row[0] for row in _BETWEEN_FAMILY])
def test_a_scope_between_two_requests_scopes_the_notes_request_it_fronts(served_notes, case, seed, text, note_ids):
    workspace, install = served_notes
    notes = install(_SEEDS[seed])
    result = _served(text, workspace)
    _assert_read_only(result, notes, _SEEDS[seed], note_ids, case)


def test_the_unit_before_the_scope_stays_with_its_forecast_and_the_scope_reaches_notes(served_notes):
    """A trailing adjunct of the first request and a scope of the second, in one message: each rides its own request."""
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    result = _served('what is the weather in Rome, in celsius please, and in the Work folder, open my Apple note "Plan"', workspace)
    _assert_read_only(result, notes, _FOLDER_SEED, [WORK_PLAN], "unit-stays-scope-rides")


def test_between_request_scopes_in_one_session_each_reach_the_notes_owner(served_notes):
    workspace, install = served_notes
    notes = install(_FOLDER_SEED)
    with _session(workspace) as turn:
        first = turn('check the gold price, and in the Personal folder, open my Apple note "Plan"')
        _assert_read_only(first, notes, _FOLDER_SEED, [HOME_PLAN], "cross-turn-first")
        notes.scripts.clear()
        second = turn('What time is it in Denver? In the Travel folder, open my Apple note "Packing list".')
        _assert_read_only(second, notes, _FOLDER_SEED, [TRAVEL_PACK], "cross-turn-second")
        notes.scripts.clear()
        third = turn('explain dew in one line, and in the Work folder, show me the Apple note "Plan" too')
        _assert_read_only(third, notes, _FOLDER_SEED, [WORK_PLAN], "cross-turn-third")

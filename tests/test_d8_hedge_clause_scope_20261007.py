"""A hedge dates nothing in ITS clause, not in the whole sentence (core/memory_receipts.py resolve_event_day).

The port's hedge law (c5d17ff0) tested the hedge over the whole sentence, so a long user sentence ending in
"maybe audiobooks will help" lost the dated event of its plain clause ("I went to the optician on 7 February") and
the receipts matched nothing for "When did I go to the optician?". The hedge is now scoped to its clause: a
hedged clause still dates nothing, a plain clause beside it keeps its day. Every sentence below was written for
this file.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from core.memory_receipts import extract_facts, resolve_event_day

STATED_DAY = date(2025, 2, 8)
STATED = datetime(2025, 2, 8, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("sentence,day", [
    ("I went to the optician on 7 February and realized I need new glasses, maybe audiobooks will help me adjust.", "2025-02-07"),
    ("Yesterday I fired the kiln; perhaps I will glaze the bowls on Sunday.", "2025-02-07"),
    ("The forestay bent two days ago, and I think the chandler is closed this week.", "2025-02-06"),
    ("i saw the dentist on 4 February, not sure the filling will hold though", "2025-02-04"),
])
def test_a_plain_clause_keeps_its_day_beside_a_hedged_clause(sentence, day):
    assert resolve_event_day(sentence, STATED_DAY)[0].isoformat() == day
    assert any(f.value_type == "event" and f.norm == day for f in extract_facts(sentence, STATED, "user")), \
        [(f.value_type, f.norm) for f in extract_facts(sentence, STATED, "user")]


@pytest.mark.parametrize("sentence", [
    "I think it was 7 February when I went to the optician.",            # hedge in the dated clause
    "Maybe yesterday the swarm went to the old chestnut, I am not sure.",  # both clauses hedged
    "Perhaps two days ago, or so, the forestay bent.",
    "I went to the optician, possibly on 7 February, and bought glasses.",  # the date sits in the hedged clause
    "I went there two days ago, or maybe three days ago, I forget.",      # an alternative hedges what it alters
])
def test_a_hedge_in_the_dated_clause_still_dates_nothing(sentence):
    assert resolve_event_day(sentence, STATED_DAY) == (None, "hedged")
    assert not any(f.value_type == "event" for f in extract_facts(sentence, STATED, "user"))


def test_a_sentence_without_a_hedge_is_untouched():
    assert resolve_event_day("I went to the optician on 7 February and bought glasses.", STATED_DAY)[0].isoformat() == "2025-02-07"

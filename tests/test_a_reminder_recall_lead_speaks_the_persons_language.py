"""A recall that names a reminder leads it in the person's language, like the told facts above it.

Gate 51 (installed 1e7b35e24, 2026-11-27): in a Lithuanian chat "You told me:" was translated while the line under it,
"You asked me to remind you:", stayed English. All names and values here are invented for this contract.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from core.i18n.lane_receipts import in_the_persons_language

CATALOGS = Path(__file__).resolve().parent.parent / "core" / "i18n" / "catalogs"


def _lead(locale: str, key: str) -> str:
    return json.loads((CATALOGS / f"{locale}.json").read_text(encoding="utf-8"))["messages"][key]


@pytest.mark.parametrize("seed", range(4))
def test_both_leads_of_a_recall_are_translated_and_the_told_words_stay(seed):
    rng = random.Random(f"remind-lead-{seed}")
    asked, locale = rng.choice([
        ("ką turiu padaryti penktadienį? pasakyk viską", "lt"),
        ("Was soll ich am Freitag erledigen? Sag mir alles", "de"),
    ])
    fact = rng.choice(["Ilse mag Zitronenkuchen", "Rūta mėgsta citrinų pyragą"])
    reminder = rng.choice(["call the glazier, Friday 04 Dec 2026 at 09:30", "paskambinti stikliui, penktadienį 09:30"])
    both = in_the_persons_language(f"You told me: {fact}\nYou asked me to remind you: {reminder}", asked)
    told, remind = both.split("\n")
    assert told == f"{_lead(locale, 'memory.you_told_me')} {fact}"
    assert remind == f"{_lead(locale, 'memory.you_asked_remind')} {reminder}"
    alone = in_the_persons_language(f"You asked me to remind you:\n- {reminder}\n- {fact}", asked)
    assert alone.split("\n") == [_lead(locale, "memory.you_asked_remind"), f"- {reminder}", f"- {fact}"]


def test_every_shipped_catalog_has_the_reminder_lead():
    for path in CATALOGS.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["messages"].get("memory.you_asked_remind"), path.stem


def test_an_english_recall_is_left_as_it_was():
    text = "You told me: the spare key is in the blue tin\nYou asked me to remind you: water the fern"
    assert in_the_persons_language(text, "what do you know about the key and the fern?") == text

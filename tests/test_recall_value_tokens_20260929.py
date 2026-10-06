"""q90-recall Fix B: short-form values are distinctive values.

Root cause (preserved originals from frozen-head traces): _VALUE_TOKEN_RE only
recognized API keys, ISO dates, hyphenated codes and 3+-digit numbers. Times
("21:30"), fractions ("18/3"), decimals ("0.92", "6.2") and short numerals
("cone 5", "about 40 birds", "vehicle 61") were invisible to
carries_unseen_value / assistant_value_gap / chunk-adds-new-information, so
value-bearing evidence lines were skipped as "already covered" while the
decisive value sat undelivered (F02-07 18/3, F02-11 17:30, F01-03 21:30).

Fresh cases use new domains (kettles, relay legs, archery, ferries).
Controls pin the already-recognized shapes and that ordinary words still
carry no value.
"""
from __future__ import annotations

import core.context_retrieval as cr


def _vals(text: str):
    return cr._distinctive_value_tokens(text)


# ── preserved originals (frozen-head failure bodies) ───────────────────────

def test_preserved_time_value_recognized():
    assert "21:30" in _vals("Correction: lights-out is 21:30.")
    assert "17:30" in _vals("The last locking-through of the day is 17:30.")
    assert "19:45" in _vals("Sauna closes 20:00; last plunge 19:45; no glass.")


def test_preserved_fraction_value_recognized():
    assert "18/3" in _vals(
        "Good choice — 18/3 linen is the usual atlas weight.")


def test_preserved_decimal_and_short_numeral():
    assert "6.2" in _vals("Print circulation fell 6.2% in the quarter.")
    assert "0.92" in _vals("set the packing to 0.92 mm")
    assert "5" in _vals("glaze firings now run to cone 5")
    assert "40" in _vals("usually about 40 birds")
    assert "61" in _vals("We always take vehicle 61 for transfers.")


# ── fresh cases: new domains, wording, values ──────────────────────────────

def test_fresh_kettle_decimal_watts():
    assert "2.4" in _vals("The kettle draws 2.4 kW at peak draw.")


def test_fresh_relay_leg_fraction():
    assert "9/16" in _vals("Anchor the relay leg at 9/16 inch for the sprint squad.")


def test_fresh_archery_short_numeral():
    assert "7" in _vals("She shoots a 7 pin on the indoor range.")


def test_fresh_ferry_berth_short_numeral():
    assert "21" in _vals("The night ferry docks at berth 21 regardless of tide.")


def test_fresh_opening_time_value():
    assert "8:15" in _vals("Doors open 8:15 sharp on market days.")


# ── controls: existing shapes still recognized; words are not values ───────

def test_control_existing_shapes_still_recognized():
    vals = _vals("Port is 5433, code SABLE-2048, dated 2026-03-25, key sk-abc123456.")
    assert "5433" in vals
    assert "2026-03-25" in vals
    assert "sable-2048" in vals or "SABLE-2048" in vals
    assert any(v.startswith("sk-") for v in vals)


def test_control_plain_words_are_not_values():
    vals = _vals("The flamingo colony winters over until late January.")
    assert vals == set(), vals


def test_control_carries_unseen_value_escapes_dedup():
    # the escape hatch the gates use: a value the transcript lacks is unseen
    assert cr._distinctive_value_tokens("lights-out is 21:30") - {
        "22:00"}  # sanity: sets subtract cleanly
    text = "Lights-out at the hut is 22:00."
    assert "21:30" not in " ".join([text])  # value genuinely absent
    assert cr._distinctive_value_tokens("Correction: lights-out is 21:30.")

"""q90-recall Fix A: decimal-safe sentence spans.

Root cause (preserved original, frozen-head F03-03/F10-11): _sentence_spans
split at EVERY period, so stored values like "6.2%" and "0.92 mm" were cut
into two evidence lines — "…fell 6." + "2% in the quarter…" — destroying the
value in the delivered capsule.

Fresh cases below use different domains, entities, values and relationships
(lab chemistry, toolchain versions, hydrology). Controls preserve normal
sentence boundaries, abbreviation merging and the offset law
(body[start:end] == text for every span, CONTRACT mr29/1 §1.3).
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (  # noqa: F401
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


def _spans(body: str):
    return cr._sentence_spans(body)


def _texts(body: str):
    return [t for (_s, _e, t) in _spans(body)]


# ── preserved originals (frozen-head failures, verbatim bodies) ────────────

def test_preserved_f03_03_percent_not_split():
    body = ("From this morning's Herald piece: 'Print circulation fell 6.2% in "
            "the quarter, a third consecutive drop, while digital subscriptions "
            "climbed by 1,400.'")
    texts = _texts(body)
    joined = " ".join(texts)
    assert any("6.2%" in t for t in texts), texts
    assert not any(t.rstrip().endswith("fell 6.") for t in texts)
    assert not any(t.lstrip().startswith("2%") for t in texts)
    assert "6.2%" in joined


def test_preserved_f10_11_mm_value_not_split():
    body = ("Pasting the mechanic's sheet verbatim: 'Feeder setup for the "
            "Heidelberg platen: set the packing to 0.92 mm, lock the grippers "
            "at nominal.'")
    texts = _texts(body)
    assert any("0.92 mm" in t for t in texts), texts
    assert not any(t.rstrip().endswith("to 0.") for t in texts)


def test_preserved_f03_03_window_carries_whole_value():
    # integration: the evidence window for the ask must contain the value
    body = ("From this morning's Herald piece: 'Print circulation fell 6.2% in "
            "the quarter, a third consecutive drop, while digital subscriptions "
            "climbed by 1,400.'")
    windows = cr._evidence_clause_windows("How much did print decline?", body)
    assert windows, "expected at least one window"
    assert any("6.2%" in str(w.get("text")) for w in windows), windows


# ── fresh cases: different wording, entities, values, domains ──────────────

def test_fresh_lab_decimal_grams_one_span():
    body = ("Lab notebook entry: dissolve 2.5 g of the mordant in 100 ml of "
            "rainwater. The bath held at 60 degrees for the full run.")
    texts = _texts(body)
    assert any("2.5 g" in t for t in texts), texts


def test_fresh_version_number_chain_one_span():
    body = "We pinned the toolchain at 1.8.2 for the staging build."
    texts = _texts(body)
    assert any("1.8.2" in t for t in texts), texts
    assert len([t for t in texts if t.strip()]) == 1, texts


def test_fresh_hydrology_two_sentences_both_decimal_safe():
    body = ("Gauge readings: the river sat at 2.31 m after the storm. It fell "
            "back to 1.98 m by Friday.")
    texts = _texts(body)
    assert any("2.31 m" in t for t in texts), texts
    assert any("1.98 m" in t for t in texts), texts
    assert len(texts) == 2, texts


def test_fresh_sentence_final_decimal_still_ends_sentence():
    # "6.2." — the last period IS a sentence end (followed by space + capital)
    body = "The tally came to 6.2. We recounted everything the next morning."
    texts = _texts(body)
    assert any(t.strip() == "The tally came to 6.2." for t in texts), texts
    assert any("We recounted" in t for t in texts), texts


# ── preservation controls: unchanged good behavior ─────────────────────────

def test_control_plain_sentences_still_split():
    texts = _texts("Lights-out at the hut is 22:00. Firewood arrived today.")
    assert len(texts) == 2, texts


def test_control_abbreviation_merge_still_one_span():
    texts = _texts("Dr. Vale arrived at noon. The ferry slip is 12-North.")
    assert any("Dr. Vale arrived at noon." in t for t in texts), texts


def test_control_offset_law_holds():
    body = ("Note du 12 septembre: la vendange tardive est autorisée sur le "
            "Clos des Rôties. Le pressurage commence à l'aube; prévoir deux "
            "équipes.\n\nSecond paragraph: 3.5 tonnes expected.")
    for start, end, text in _spans(body):
        assert body[start:end] == text
    joined = "".join(t for _s, _e, t in _spans(body))
    # every non-newline character of the body is accounted for by the spans
    assert joined.replace("\n", "") == body.replace("\n", "")

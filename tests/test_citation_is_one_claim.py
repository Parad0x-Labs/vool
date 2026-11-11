"""A parenthetical citation is one claim, never two halves.

Measured 2026-11-05 (operator's live 0.7.8 test, turn conductor-fbe48fc26537): the FX citation
"USD/EUR: 1 USD = 0.88945 EUR (source: Frankfurter public institutional rates; observed:
2026-11-05T00:00:00+00:00)." was cut at the ";" INSIDE the parenthetical — the tail
"observed: …)." became a claim of its own, matched no source, and the publication gate
"withheld" half a citation. The served answer carried a dangling "(source: … rates;", and the
withheld banner quoted the orphaned fragment back at the operator as if it were a statement.
The claim splitter must treat a balanced parenthetical as one unit: its separators are the
citation's own punctuation, never claim boundaries.
"""

from __future__ import annotations

from core.claim_support import segment_claims

FX_CITATION = (
    "USD/EUR: 1 USD = 0.88945 EUR (source: Frankfurter public institutional rates; "
    "observed: 2026-11-05T00:00:00+00:00)."
)


def test_a_parenthetical_citation_stays_one_segment() -> None:
    segments = segment_claims(FX_CITATION)
    assert segments == [FX_CITATION], segments


def test_a_weather_citation_with_semicolon_stays_whole() -> None:
    segments = segment_claims("Saint-Merri, France: Partly Cloudy, 11.0°C (source: wttr.in; observed: now).")
    assert len(segments) == 1, segments
    assert "observed" in segments[0]


def test_ordinary_coordinated_clauses_still_split() -> None:
    """The splitter's real job — top-level ', and' / ', but' / ';' — is unchanged."""
    segments = segment_claims("Gold is dearer in Europe, and silver is cheaper in Asia; the spreads widened.")
    assert len(segments) == 3, segments


def test_bracketed_spans_are_masked_too() -> None:
    segments = segment_claims("The rate held [see the ECB note; revised daily], and the spread narrowed.")
    assert len(segments) == 2, segments
    assert any("[see the ECB note; revised daily]" in segment for segment in segments)


def test_nested_parenthetical_does_not_confuse_the_mask() -> None:
    segments = segment_claims("The pair held (source: a bank; sub-note: inner; text), and it moved.")
    assert len(segments) == 2, segments
    assert any("sub-note: inner" in segment for segment in segments)

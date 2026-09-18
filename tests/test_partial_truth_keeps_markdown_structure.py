"""The publication gate adjudicates claims; it must not shred the markdown that carries them.

Measured live 2026-09-07 (served comparison, free cloud model, 16 supported / 22 withheld): the
title "# Volkswagen Golf vs. Toyota Prius: Head-to-Head" was cut at "vs." and half of it withheld;
every "## ..." heading was adjudicated as a claim and withheld; a bullet "- **Prius**: ..." lost
its label and left a dangling "**" in the published text. None of that is gating -- it is damage.
Headings are structure, abbreviations end no sentence, and emphasis markers belong to the words
they wrap.
"""
from __future__ import annotations

from core.claim_support import match_claims, segment_claims
from core.grounding_publication import compose_partial_truth

REQUEST = "compare the vw passat and the vw golf in detail: production periods, sales, regions, engines and prices"
NOTES = [
    {"result_title": "Volkswagen Passat production history", "result_url": "https://autoarchive-fixture.org/passat-production", "origin_domain": "autoarchive-fixture.org",
     "summary": "Updated 2026-08-20. The Volkswagen Passat has been produced since 1973. Generations: B1 1973-1980, B8 2014-2023, B9 since 2023."},
    {"result_title": "Volkswagen Golf production history", "result_url": "https://autoarchive-fixture.org/golf-production", "origin_domain": "autoarchive-fixture.org",
     "summary": "Updated 2026-08-18. The Volkswagen Golf has been produced since 1974. Generations: Mk1 1974-1983, Mk8 since 2019 with a 2024 facelift."},
    {"result_title": "Volkswagen model sales totals", "result_url": "https://salesdata-fixture.org/vw-model-sales", "origin_domain": "salesdata-fixture.org",
     "summary": "Updated 2026-07-31. Cumulative sales: Golf about 37 million units worldwide by end of 2024. Passat about 34 million units worldwide by end of 2024."},
]

ANSWER = "\n".join([
    "# Volkswagen Passat vs. Volkswagen Golf: Head-to-Head",
    "",
    "## Production",
    "- **Passat**: production began in 1973. **Golf**: production began in 1974.",
    "",
    "## Sales",
    "Golf about 37 million units by end of 2024, Passat about 34 million.",
    "",
    "## Engines",
    "- **Passat**: the B9 offers a 2.0 TSI with 265 PS. **Golf**: the Golf R has 333 PS.",
])


def test_headings_are_structure_not_claims() -> None:
    assert segment_claims("## Engines") == []
    assert segment_claims("# Volkswagen Passat vs. Volkswagen Golf: Head-to-Head") == []


def test_an_abbreviation_does_not_end_a_sentence() -> None:
    segments = segment_claims("The Passat vs. the Golf is a fair fight. Both sold well, e.g. in Europe.")
    assert segments == ["The Passat vs. the Golf is a fair fight.", "Both sold well, e.g. in Europe."], segments


def test_emphasis_markers_stay_with_their_words() -> None:
    segments = segment_claims("- **Passat**: production began in 1973. **Golf**: production began in 1974.")
    assert all("*" not in s for s in segments), segments
    assert segments[0].startswith("Passat: production began in 1973"), segments


def test_a_gated_comparison_keeps_its_headings_and_labels_and_only_loses_the_unsupported_sentence() -> None:
    claim_map = match_claims(answer=ANSWER, notes=NOTES, request_text=REQUEST)
    statuses = {c.text[:20]: c.status for c in claim_map.claims}
    assert any(s == "unsupported" for s in statuses.values()), statuses
    body, withheld, _unverifiable = compose_partial_truth(ANSWER, claim_map)
    # the title survives whole, on one line
    assert "# Volkswagen Passat vs. Volkswagen Golf: Head-to-Head" in body.splitlines(), body
    # headings whose sections still carry content survive; no heading is ever listed as withheld
    assert "## Production" in body and "## Sales" in body, body
    assert not any(w.startswith("#") or w.startswith("Production") or w.startswith("Sales") for w in withheld), withheld
    # the bullet keeps its bold labels intact -- no dangling markers anywhere in the published text
    assert "**Passat**: production began in 1973." in body, body
    assert "** " not in body and not any(line.strip() == "**" for line in body.splitlines()), body
    # the unsupported engine figures are withheld; the heading that introduced ONLY them does not stand orphaned
    assert any("265 PS" in w for w in withheld), withheld
    assert "## Engines" not in body, body

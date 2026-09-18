"""A mixed prose line keeps its supported sentences; only the unsupported sentence is withheld.

Measured 2026-09-06 (served comparison, fixture transport): the answer was ONE paragraph carrying
seven adjudicated claims -- six supported, one unsupported (the engines sentence whose figures the
snippet had cut). Line-level composition dropped the whole paragraph ("every supported claim lived
on a line that also carried a fabricated one"), the map was forced to `none`, and the reader got a
full refusal for a turn whose sources supported six of seven statements. Tables already had a
cell-level rule for exactly this; prose gets the sentence-level one.
"""
from __future__ import annotations

from core.claim_support import match_claims
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
ONE_PARAGRAPH = (
    "Golf production began in 1974 and Passat production in 1973. "
    "Sales: Golf about 37 million units by end of 2024, Passat about 34 million. "
    "Engines: Passat B9 offers a 2.0 TSI with 265 PS and the Golf R has 333 PS."
)


def test_a_mixed_paragraph_keeps_its_supported_sentences_and_withholds_the_unsupported_one() -> None:
    claim_map = match_claims(answer=ONE_PARAGRAPH, notes=NOTES, request_text=REQUEST)
    assert claim_map.coverage == "partial", [(c.status, c.text[:40]) for c in claim_map.claims]
    body, withheld, unverifiable = compose_partial_truth(ONE_PARAGRAPH, claim_map)
    assert "1974" in body and "37 million" in body, body
    assert "265 PS" not in body and "333 PS" not in body, body
    assert any("265 PS" in w for w in withheld), withheld
    assert ".;" not in body and ";." not in body, body


def test_a_line_whose_unsupported_sentence_cannot_be_located_is_still_dropped_whole() -> None:
    """The fallback stays: a sentence carrying a link is not a verbatim segment, so the line
    cannot be trimmed safely and is withheld as before (links are never silently dropped)."""
    text = ("Golf production began in 1974 and Passat production in 1973. "
            "Engines: see https://motorspec-fixture.org/passat-b9-engines for the Passat B9 2.0 TSI 265 PS.")
    claim_map = match_claims(answer=text, notes=NOTES, request_text=REQUEST)
    body, withheld, _ = compose_partial_truth(text, claim_map)
    assert withheld, "the linked engines sentence is unsupported"
    assert body.strip() == "" or "265 PS" not in body

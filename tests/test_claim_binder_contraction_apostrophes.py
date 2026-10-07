"""Apostrophes inside contractions are not quote marks for the claim binder.

Measured 2026-10-07 on a held-out memory run: "I've been thinking about reading more, but I just bought a rear rack
for $79 this week and I'm not sure ..." was read as a quotation (the single-quote arm spanned "I've ... I'm"), the
record stopped counting as the user's own statement, and a correct average of three purchases was withdrawn by the
current-claim guard. The single-quote arm of `core.evidence_kernel.claim_binder._QUOTE_RE` now needs quote marks that
are not inside a word on either side; a real single-quoted span still marks a quotation.
"""
from __future__ import annotations

import pytest

from core.evidence_kernel.claim_binder import bind_claims, evidence_records


@pytest.mark.parametrize("line", [
    "I've been thinking about reading more, but I just bought a rear rack for $79 this week and I'm not sure how to fit it in. (stated: 2026-02-08)",
    "I'm so glad I bought the rear rack for $79, it's great and I'd do it again. (stated: 2026-02-08)",
    "Don't laugh, but I paid $79 for a rear rack and I can't return it. (stated: 2026-02-08)",
], ids=["ive-im", "im-its-id", "dont-cant"])
def test_contraction_apostrophes_do_not_make_a_quotation(line):
    recs = evidence_records([line])
    assert recs and not recs[0].quoted and recs[0].usable


def test_real_single_quotes_still_mark_a_quotation():
    recs = evidence_records(["A forum post reads 'my rear rack was $79' and I'm not sure I believe it. (stated: 2026-02-08)"])
    assert recs and recs[0].quoted


def test_the_average_binds_with_a_contraction_in_one_record():
    evidence = (
        "<retrieved_context>\n"
        "- user said: I've been trying to get my budget in order lately because I recently bought a bike computer for $165. (stated: 2025-04-06)\n"
        "- user said: I've been thinking about getting back into reading more, but I just bought a rear rack for $79 this week and I'm not sure how to fit reading in. (stated: 2025-02-08)\n"
        "- user said: I bought a saddle bag for $36 last weekend, and I'd like to make cleaning easier. (stated: 2025-03-17)\n"
        "</retrieved_context>"
    )
    result = bind_claims(
        question="What is the average price of the cycling items I bought this year?",
        reply="$93.33 — bike computer $165, rear rack $79, saddle bag $36 (total $280 ÷ 3).",
        evidence_text=evidence,
    )
    assert result.all_supported, result.as_dict()

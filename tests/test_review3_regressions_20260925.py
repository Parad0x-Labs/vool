"""Round-3 review regressions (independent review of b469bf4a, 2026-09-25).

Preserved verbatim from artifacts/memrepair-focused-review3-20260925/
test_review3_fresh.py (sha256 e47d4bf509f290cad476f0b76a470dfac5f4e84b3b5d8bd219b15e8a9b754442).
The two generic-code selection cases failed on b469bf4a: the span rewrite had
silently changed the generic-code fallback from last DISTINCT value (dedup by
first occurrence) to last raw occurrence, so a later incidental repetition of
an older value won selection and donated its record's date. The SOL-provenance
and unicode/empty-record cases passed and are retained as passing evidence.
"""
"""Frozen independent review: selection compatibility and span provenance."""
from core import context_retrieval as cr

def test_generic_code_repetition_preserves_last_distinct_selection():
    text="First token AL-318. Second token BK-629. Historical echo AL-318."
    lines=cr._known_fact_lines("What is the code?",text)
    assert "- exact code: BK-629" in lines,lines

def test_generic_code_repetition_preserves_selected_record_and_date():
    records=[
        ("Logged 2024-03-12\nToken AL-318.",0.9),
        ("Logged 2024-09-26\nToken BK-629.",0.8),
        ("Logged 2025-06-03\nHistorical echo AL-318.",0.7),
    ]
    out,tel=cr._distill_retrieved_hits("What is the code?",records)
    assert "exact code: BK-629" in out,out
    assert "2024-09-26" in out and "2025-06-03" not in out,out

def test_spend_cap_provenance_follows_selected_declaration():
    records=[
        ("Logged 2024-11-02\nThe current spend cap is 4 SOL.",0.95),
        ("Logged 2025-08-05\nA discarded worksheet mentions 14 SOL.",0.7),
    ]
    out,tel=cr._distill_retrieved_hits("When was the spend cap set?",records)
    assert "latest spend cap: 4 SOL" in out,out
    assert "2024-11-02" in out and "2025-08-05" not in out,out

def test_span_offsets_handle_unicode_and_empty_preceding_record():
    records=[
        ("",0.5),
        ("Résumé filed 2024-12-03\nThe vault code is VX-742.",0.95),
        ("Logged 2025-04-11\nA receipt mentions VX-742 beside spare hinges.",0.7),
    ]
    out,tel=cr._distill_retrieved_hits("When was the vault code recorded?",records)
    assert "exact code: VX-742" in out,out
    assert "2024-12-03" in out and "2025-04-11" not in out,out

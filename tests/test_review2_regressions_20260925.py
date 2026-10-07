"""Round-2 review regressions (independent review of bc621833, 2026-09-25).

Preserved verbatim from artifacts/memrepair-focused-review2-20260925/
test_review2_fresh.py (sha256 06fab48886809f2354656635a2a2704725125d61723b7b4cfa3f67b652ef2fed).
These five cases failed on bc621833 (candidate 1/5): the fallback branch
serialized (content, score) tuples into fact lines, and known-fact provenance
was re-inferred from the LAST substring occurrence of the extracted value, so
a value quoted in a later unrelated record donated that record's date.
"""
"""Frozen independent review: fallback serialization and source ownership."""
from core import context_retrieval as cr


def test_fallback_contains_only_source_text():
    out, tel = cr._distill_retrieved_hits("Summarize", [("Orchard trees survived winter.", 0.987654)])
    assert tel["selected_facts"] == ["- retrieved fact: Orchard trees survived winter."], out

def test_fallback_normalizes_real_linebreaks_without_tuple_serialization():
    out, tel = cr._distill_retrieved_hits("Summarize", [("Lilacs bloomed.\nFinches nested.", 0.876543)])
    assert tel["selected_facts"] == ["- retrieved fact: Lilacs bloomed. Finches nested."], out

def test_code_provenance_does_not_follow_a_substring_in_another_record():
    records = [
        ("Logged on 2024-02-16\nThe access code is QV-728.", 0.95),
        ("Logged on 2025-11-04\nShipment reference QV-7280 was archived.", 0.8),
    ]
    out, tel = cr._distill_retrieved_hits("When was the access code assigned?", records)
    assert "exact code: QV-728" in out, out
    assert "2024-02-16" in out and "2025-11-04" not in out, out

def test_code_provenance_tracks_declaration_not_incidental_repeated_value():
    records = [
        ("Logged on 2023-08-21\nThe access code is NM-439.", 0.96),
        ("Logged on 2026-01-12\nThe label NM-439 appeared in an unrelated packing checklist.", 0.7),
    ]
    out, tel = cr._distill_retrieved_hits("When was the access code assigned?", records)
    assert "exact code: NM-439" in out, out
    assert "2023-08-21" in out and "2026-01-12" not in out, out

def test_unrelated_record_without_shared_value_does_not_change_provenance():
    records = [
        ("Logged on 2024-09-18\nThe access code is KP-861.", 0.93),
        ("Logged on 2025-03-07\nThe garden gate was painted.", 0.6),
    ]
    out, tel = cr._distill_retrieved_hits("When was the access code assigned?", records)
    assert "2024-09-18" in out and "2025-03-07" not in out, out

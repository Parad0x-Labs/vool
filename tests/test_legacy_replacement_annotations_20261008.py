"""Legacy suffix filtering preserves authored marker text across rendered fact types."""
import json

import pytest

from core.evidence_compiler import Packet, _render_line, filter_packet, obligations, render


@pytest.mark.parametrize("kind", ["count", "state", "mention", "preference", "event", "list"])
@pytest.mark.parametrize("has_note", [False, True])
def test_legacy_annotation_search_starts_after_the_complete_authored_body(kind, has_note):
    literal = '41 crowns  [REPLACED later by "blue hook"'
    value = json.dumps([literal]) if kind == "list" else literal
    fact = {
        "value_type": kind, "value": value, "sentence": "The rent label is " + literal + ".",
        "norm": "rent=" + value, "replaced_by": "r:later", "replaced_by_occurrence": "u:later",
        "receipt_id": "r:old", "occurrence_id": "u:old", "role": "user",
    }
    receipts = {
        "r:old": {"role": "user", "statement_at": 1760000000},
        "r:later": {"role": "user", "statement_at": 1790000000,
                    "changes": [{"old_value": value, "new_value": "47 crowns"}]},
    }
    line = _render_line(receipts["r:old"], fact, receipts)
    # Legacy rows lack the renderer's separate body; retain the actual authored line as the oracle.
    bare = fact.pop("line_without_replacement")
    own_line = line.split('  [REPLACED later by "47 crowns"', 1)[0]
    packet = Packet(obligation=obligations("How much was the rent?"),
                    lines=[line if has_note else own_line], facts=[fact])
    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})
    assert filtered.lines == [bare]
    assert literal in render(filtered)


@pytest.mark.parametrize("newer", [
    '47 crowns  [REPLACED later by "inner marker"',
    '47 "gold" crowns\n  [REPLACED later by "inner marker"',
    '47 crowns  [REPLACED later by "inner" stated 2026-12-01; not current]',
])
def test_legacy_annotation_removes_nested_quoted_markers_as_one_suffix(newer):
    from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _rent_rows

    packet = _rent_rows(newer)
    bare = packet.facts[0].pop("line_without_replacement")
    assert newer in render(packet)
    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})
    assert filtered.lines == [bare]
    assert "47 crowns" not in render(filtered)

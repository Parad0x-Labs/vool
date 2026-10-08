"""Legacy replacement dependencies are resolved structurally or withheld whole."""
from copy import deepcopy

import pytest

from core.evidence_compiler import Packet, _render_line, filter_packet, obligations, render


def _row(*, value_type="count", replacement_identity=True):
    fact = {
        "value_type": value_type, "value": "41 crowns", "norm": "rent=41 crowns",
        "sentence": "The rent is 41 crowns until 2026-08-01.",
        "replaced_by": "r:later", "receipt_id": "r:old", "occurrence_id": "u:old", "role": "user",
    }
    if replacement_identity:
        fact["replaced_by_occurrence"] = "u:later"
    receipts = {
        "r:old": {"role": "user", "statement_at": 1770109200,
                  "changes": [{"old_value": "39 crowns", "new_value": "41 crowns",
                               "old_statement_at": 1767258000}]},
        "r:later": {"role": "user", "statement_at": 1793955600,
                    "changes": [{"old_value": "41 crowns", "new_value": "47 crowns"}]},
    }
    line = _render_line(receipts["r:old"], fact, receipts)
    packet = Packet(obligation=obligations("As of September how much was the rent?"),
                    lines=[line], facts=[fact])
    return packet, receipts


def test_legacy_missing_replacement_identity_is_migrated_or_withheld():
    packet, _ = _row(replacement_identity=False)
    assert "47 crowns" in render(packet)

    filtered = filter_packet(packet, {"u:later": "temporal:future-relative-to-as-of"})

    assert "47 crowns" not in render(filtered)
    assert not filtered.facts
    assert filtered.telemetry["complete"] is False
    assert filtered.telemetry["refused_rows"][0]["reason"] == "replacement-provenance-unavailable"


def test_legacy_state_loses_refused_replacement_date():
    packet, _ = _row(value_type="state")
    bare = packet.facts[0].pop("line_without_replacement")
    assert "until 2026-11-06" in render(packet)

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert "until 2026-11-06" not in render(filtered)
    assert filtered.lines == [bare]
    assert "until 2026-08-01" in render(filtered)
    assert 'replaces "39 crowns"' in render(filtered)
    assert "47 crowns" not in render(filtered)
    assert "1793955600" not in str(filtered.facts)


@pytest.mark.parametrize("source", ["source_receipts", "peer_fact", "rendering_metadata"])
def test_legacy_replacement_identity_migrates_from_structured_sources(source):
    packet, receipts = _row(replacement_identity=False)
    bare = packet.facts[0]["line_without_replacement"]
    later = {**receipts["r:later"], "receipt_id": "r:later", "occurrence_id": "u:later"}
    if source == "source_receipts":
        packet.source_receipts = {"r:later": later}
    elif source == "peer_fact":
        packet.facts.append(later)
        packet.lines.append("- Later rent: 47 crowns")
    else:
        packet.lines[0] = _render_line(receipts["r:old"], packet.facts[0], {**receipts, "r:later": later})
    before = deepcopy(packet)

    filtered = filter_packet(packet, {"u:later": "temporal:future-relative-to-as-of"})

    assert filtered.facts[0]["occurrence_id"] == "u:old"
    assert filtered.lines[0] == bare
    assert "47 crowns" not in render(filtered)
    assert packet == before


def test_unresolved_replacement_is_withheld_even_without_a_refusal_entry():
    packet, _ = _row(replacement_identity=False)

    filtered = filter_packet(packet, {})

    assert not filtered.facts
    assert "crowns" not in render(filtered)
    assert filtered.telemetry["complete"] is False


def test_conflicting_replacement_identities_withhold_the_whole_row():
    packet, receipts = _row()
    packet.source_receipts = {"r:later": {**receipts["r:later"], "occurrence_id": "u:different"}}

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert not filtered.facts
    assert "crowns" not in render(filtered)


def test_structural_migration_keeps_a_valid_replacement_and_survives_a_second_filter():
    packet, receipts = _row(replacement_identity=False)
    packet.source_receipts = {"r:later": {**receipts["r:later"], "occurrence_id": "u:later"}}

    migrated = filter_packet(packet, {"u:other": "temporal:withdrawn"})
    filtered = filter_packet(migrated, {"u:later": "temporal:withdrawn"})

    assert migrated.facts[0]["replaced_by_occurrence"] == "u:later"
    assert migrated.lines == packet.lines
    assert "47 crowns" in render(migrated)
    assert "47 crowns" not in render(filtered)
    assert "41 crowns" in render(filtered)


def test_an_unrelated_refusal_preserves_authored_and_replacement_dates():
    packet, _ = _row(value_type="state")
    packet.facts[0].pop("line_without_replacement")

    filtered = filter_packet(packet, {"u:other": "temporal:withdrawn"})

    assert filtered is packet
    assert "until 2026-08-01" in render(filtered)
    assert "until 2026-11-06" in render(filtered)
    assert "47 crowns" in render(filtered)

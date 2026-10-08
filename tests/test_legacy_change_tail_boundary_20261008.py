"""Replacement removal preserves authored change history and refuses ambiguous legacy rows."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from core.evidence_compiler import Packet, _render_line, filter_packet, obligations, render


def _ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def _row(old_value, *, value_type="count", newer="47 crowns"):
    fact = {
        "value_type": value_type, "value": "41 crowns", "sentence": "The rent is 41 crowns.",
        "norm": "rent=41 crowns", "replaced_by": "r:later", "replaced_by_occurrence": "u:later",
        "receipt_id": "r:old", "occurrence_id": "u:old", "role": "user",
    }
    receipts = {
        "r:old": {
            "role": "user", "statement_at": _ts("2026-02-03T09:00:00"),
            "changes": [{"old_value": old_value, "new_value": "41 crowns",
                         "old_statement_at": _ts("2026-01-01T09:00:00")}],
        },
        "r:later": {
            "role": "user", "statement_at": _ts("2026-11-06T09:00:00"),
            "changes": [{"old_value": "41 crowns", "new_value": newer}],
        },
    }
    line = _render_line(receipts["r:old"], fact, receipts)
    bare = fact.pop("line_without_replacement")
    packet = Packet(obligation=obligations("How much was the rent?"), lines=[line], facts=[fact])
    return packet, bare, receipts


@pytest.mark.parametrize("old_value", [
    '39 crowns  [REPLACED later by "blue hook"',
    '39 "bronze" crowns\n  [REPLACED later by "violet hook"',
    '39 crowns  [REPLACED later by "inner" stated 2026-08-01; not current]',
])
def test_legacy_fallback_preserves_the_earlier_receipts_own_change_tail(old_value):
    packet, bare, _ = _row(old_value)
    assert old_value in bare and "47 crowns" not in bare

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert filtered.lines == [bare]
    assert old_value in render(filtered)


def test_every_prior_change_and_event_annotation_survives_nested_later_values():
    packet, _, receipts = _row('39 crowns  [REPLACED later by "blue hook"', newer='47 "gold" crowns\n[new value]')
    fact = packet.facts[0]
    fact.update(event_at=_ts("2026-02-01T09:00:00"), event_grain="day")
    receipts["r:old"]["changes"].append({
        "old_value": '40 crowns  [REPLACED later by "second hook"',
        "new_value": "41 crowns", "old_statement_at": _ts("2026-01-31T09:00:00"),
    })
    packet.lines = [_render_line(receipts["r:old"], fact, receipts)]
    bare = fact.pop("line_without_replacement")

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert filtered.lines == [bare]
    assert 'second hook' in filtered.lines[0] and "event day 2026-02-01" in filtered.lines[0]


@pytest.mark.parametrize("value_type", ["count", "state"])
def test_metadata_free_legacy_row_migrates_from_its_source_receipt(value_type):
    packet, bare, receipts = _row('39 crowns  [REPLACED later by "blue hook"', value_type=value_type)
    packet.facts[0].pop("line_source", None)
    packet.source_receipts = receipts

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert filtered.lines == [bare]
    assert "until 2026-11-06" not in filtered.lines[0]


@pytest.mark.parametrize("line_source", [None, {"version": 1, "receipt": {}}, {"version": 9}])
def test_legacy_row_without_recoverable_source_is_withheld_whole(line_source):
    packet, _, _ = _row('39 crowns  [REPLACED later by "blue hook"')
    packet.facts[0].pop("line_source", None)
    if line_source is not None:
        packet.facts[0]["line_source"] = line_source
    packet.telemetry = {"coverage": {"candidates": 1, "shown": 1, "exhaustive": True}, "complete": True}
    original = deepcopy(packet)

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert not filtered.facts
    assert "crowns" not in render(filtered) and "blue hook" not in render(filtered)
    assert filtered.telemetry["complete"] is False
    assert filtered.telemetry["coverage"]["shown"] == 0
    assert filtered.telemetry["refused_rows"][0]["reason"] == "replacement-provenance-unavailable"
    assert packet.lines == original.lines and packet.facts == original.facts


def test_legacy_explicit_bare_representation_remains_a_lossless_source():
    packet, bare, _ = _row('39 crowns  [REPLACED later by "blue hook"')
    packet.facts[0].pop("line_source", None)
    packet.facts[0]["line_without_replacement"] = bare

    filtered = filter_packet(packet, {"u:later": "temporal:withdrawn"})

    assert filtered.lines == [bare]

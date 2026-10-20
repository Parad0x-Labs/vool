from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest

from core.my_things.prepare import packet, prepare, warranty
from core.my_things.store import ThingsError, ThingsStore, file_payload
from tests.my_things.test_served import call, daemon, file


def record():
    return {"name": "Synthetic radio", "model": "RX-9112", "bought_on": "2026-01-01", "bought_from": "Example shop", "price": "49.00 EUR", "warranty_end": "2027-01-01", "specs": "Voltage: 5 V\nConnector: USB-C", "order_items": "Radio\nCable", "notes": "Private unrelated personal note", "files": [file_payload("receipt.txt", b"Synthetic invoice RIVER-4231", "receipt"), file_payload("manual.txt", b"Private unrelated instructions", "manual")]}


def test_warranty_uses_only_saved_date_and_missing_is_unknown():
    assert warranty(record(), today=date(2027, 1, 1))["state"] == "within"
    assert warranty(record(), today=date(2027, 1, 2))["state"] == "expired"
    assert warranty({"bought_on": "2026-01-01"})["state"] == "unknown"


def test_packets_contain_drafts_and_chosen_evidence_not_unrelated_manuals():
    result = prepare(record(), kind="repair", issue="The dial no longer turns")
    assert result["sent"] is False and result["draft_only"] is True
    assert "49.00 EUR" in result["draft"] and "eligibility" in result["draft"]
    with zipfile.ZipFile(io.BytesIO(packet(record(), kind="repair", issue="The dial no longer turns"))) as archive:
        assert archive.read("request-draft.txt").decode() == result["draft"]
        assert b"Synthetic invoice RIVER-4231" in [archive.read(n) for n in archive.namelist()]
        assert all(b"Private unrelated instructions" not in archive.read(n) for n in archive.namelist())
        assert b"Private unrelated personal note" not in archive.read("facts.json")
    with pytest.raises(ThingsError):
        packet(record(), kind="repair", issue="")


def test_fits_never_guesses_specs_or_claims_compatibility():
    result = prepare(record(), kind="fits", observed="Voltage: 12 V\nConnector: USB-C\nDiameter: 3 mm")
    assert result["matches"] == ["connector: USB-C"]
    assert result["conflicts"] == ["voltage: 5 V / 12 V"]
    assert "diameter" in result["unknown"] and "not been verified" in result["unknown"][-1]
    assert prepare(record(), kind="fits", observed="RX-9112")["matches"] == []


def test_delivery_note_cannot_confirm_arrival_and_unknown_picks_refuse():
    result = prepare(record(), kind="delivery", observed="Radio Cable", arrived=["Radio"])
    assert result["mentioned_on_note"] == ["Radio", "Cable"]
    assert result["not_confirmed_arrived"] == ["Cable"]
    assert prepare(record(), kind="delivery", observed="Radio Cable", arrived=[])["not_confirmed_arrived"] == ["Radio", "Cable"]
    with pytest.raises(ThingsError):
        prepare(record(), kind="delivery", arrived=["Invented"])


def test_served_drafts_packets_comparisons_and_unknown_warranty(daemon):
    base, _ = daemon
    fields = {k: v for k, v in record().items() if k != "files"}
    item = call(base, "save", {"fields": fields, "confirm": True, "file": file("receipt.txt", "Synthetic invoice RIVER-4231")})[1]["thing"]
    for kind in ("return", "repair", "warranty", "fits", "delivery"):
        status, result, _ = call(base, "prepare", {"id": item["id"], "kind": kind, "issue": "The dial does not turn", "observed": "Voltage: 12 V", "arrived": []})
        assert status == 200 and result["sent"] is False
    assert call(base, "packet", {"id": item["id"], "kind": "repair", "issue": "The dial does not turn"})[0] == 200
    assert call(base, "packet", {"id": item["id"], "kind": "repair", "issue": ""})[0] == 400
    call(base, "delete", {"id": item["id"], "revision": 1, "confirm": True})

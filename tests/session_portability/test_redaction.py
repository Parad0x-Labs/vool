"""Redaction: credentials, secrets and private paths never leave in a bundle.

The write-time scrubbers guard TODAY's rows; the export pass is the last line of defense for
legacy rows and any writer that missed the scrub. Tests here plant material through paths that
bypass the write-time guards and prove the EXPORTED bundle is clean.
"""

from __future__ import annotations

import json

import pytest

from tests.session_portability import support
from tests.session_portability.support import PLANTED_PRIVATE_PATH, PLANTED_SECRET, SESSION


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.plant_legacy_secret_turn()
    support.seed_tool_receipt(raw_secret_argument=True)
    support.seed_attachment()


def _flat_texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _flat_texts(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in _flat_texts(v)]
    return []


def test_planted_secret_and_private_path_never_export(tmp_path):
    from core.session_portability import api

    out = tmp_path / "s.voolsession"
    api.export_session(SESSION, out)
    payload = api.load_payload(out)
    blob = json.dumps(payload)

    assert PLANTED_SECRET not in blob
    assert PLANTED_PRIVATE_PATH not in blob
    assert "example-user" not in blob
    assert "[redacted" in blob or "[export-redacted]" in blob


def test_preview_reports_the_redaction_honestly(tmp_path):
    from core.session_portability import api

    preview = api.preview_export(SESSION)
    assert preview["redactions"] >= 1


def test_tool_receipt_secret_argument_is_scrubbed_but_the_host_survives(tmp_path):
    from core.session_portability import api

    out = tmp_path / "s.voolsession"
    api.export_session(SESSION, out)
    payload = api.load_payload(out)

    receipt = payload["receipts"]["tool_receipts"][0]
    flat = json.dumps(receipt)
    assert PLANTED_SECRET not in flat
    assert receipt["arguments"]["host"] == "api.weather.gov"


def test_bundle_file_bytes_themselves_carry_no_secret(tmp_path):
    """The zip is scanned as bytes: even a redaction bug cannot be hidden inside a member."""
    from core.session_portability import api

    out = tmp_path / "s.voolsession"
    api.export_session(SESSION, out)
    raw = out.read_bytes()

    assert PLANTED_SECRET.encode() not in raw
    assert PLANTED_PRIVATE_PATH.encode() not in raw

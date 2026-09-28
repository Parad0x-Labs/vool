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


# --- recovery-phrase export protection (wave2 export guards) ------------------------
#
# The export redaction pass can only see a recovery phrase when the canonical BIP-39
# wordlist ships (package data). These tests prove the OWNING public workflow at the
# export boundary: masked while armed, the whole export refused while protection is down
# (no bundle is emitted, pre-existing files are preserved), and in-process recovery.


def _valid_mnemonic() -> str:
    from core.wallet.mnemonic import generate_mnemonic

    return generate_mnemonic(strength_bits=128)  # a real checksum-valid 12-word phrase


def _degrade_phrase_protection(monkeypatch) -> None:
    """Make the wordlist unavailable at its real loading authority, cache reset deliberately."""
    import core.secret_redaction as sr

    def _missing() -> list[str]:
        raise FileNotFoundError("canonical wordlist missing (simulated broken install)")

    monkeypatch.setattr(sr, "_read_bip39_wordlist", _missing)
    monkeypatch.setattr(sr, "_BIP39_INDEX", None)


def test_recovery_phrase_is_masked_and_the_bundle_roundtrips_while_armed(tmp_path):
    phrase = _valid_mnemonic()
    support.seed_turns(turns=[("recover my wallet", f"the phrase is {phrase} do not lose it")])
    from core.session_portability import api

    out = tmp_path / "armed.voolsession"
    api.export_session(SESSION, out)
    blob = json.dumps(api.load_payload(out))

    assert phrase not in blob
    assert "[redacted-mnemonic]" in blob


def test_export_refuses_the_whole_bundle_while_phrase_protection_is_down(tmp_path, monkeypatch):
    from core.session_portability.api import PortabilityRefused

    phrase = _valid_mnemonic()
    support.seed_turns(turns=[("recover my wallet", f"the phrase is {phrase} do not lose it")])
    from core.session_portability import api

    out = tmp_path / "pre-existing.voolsession"
    out.write_bytes(b"pre-existing sentinel bytes")
    _degrade_phrase_protection(monkeypatch)

    with pytest.raises(PortabilityRefused) as raised:
        api.preview_export(SESSION)
    assert raised.value.code == "SECRET_PROTECTION_UNAVAILABLE"

    with pytest.raises(PortabilityRefused) as raised:
        api.export_session(SESSION, out)
    assert raised.value.code == "SECRET_PROTECTION_UNAVAILABLE"
    # no bundle was emitted: the pre-existing file at the exact output path is untouched
    assert out.read_bytes() == b"pre-existing sentinel bytes"
    assert list(tmp_path.glob("*.voolsession")) == [out]


def test_export_recovers_in_process_once_protection_returns(tmp_path, monkeypatch):
    from core.session_portability.api import PortabilityRefused

    phrase = _valid_mnemonic()
    support.seed_turns(turns=[("recover my wallet", f"the phrase is {phrase} do not lose it")])
    from core.session_portability import api

    _degrade_phrase_protection(monkeypatch)
    with pytest.raises(PortabilityRefused):
        api.export_session(SESSION, tmp_path / "never-written.voolsession")
    assert not (tmp_path / "never-written.voolsession").exists()

    monkeypatch.undo()  # restore the real wordlist reader — same process
    import core.secret_redaction as sr

    sr._BIP39_INDEX = None  # drop any cache so the next call loads through the real reader

    out = tmp_path / "recovered.voolsession"
    api.export_session(SESSION, out)
    blob = json.dumps(api.load_payload(out))
    assert phrase not in blob
    assert "[redacted-mnemonic]" in blob

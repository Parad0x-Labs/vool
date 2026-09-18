"""Authenticated encryption and tamper rejection.

An encrypted bundle is AES-256-GCM over the whole bundle with a PBKDF2-derived key, in the same
idiom the repo's credential vault uses. Tampering with ANY manifest-covered member must be refused
with a typed error naming the mismatched entry — before an import writes anything.
"""

from __future__ import annotations

import json
import shutil
import zipfile

import pytest

from tests.session_portability import support
from tests.session_portability.support import SESSION

PASSPHRASE = "correct horse battery staple"
WRONG_PASSPHRASE = "tracer-tongue"


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.seed_tool_receipt()


def _export(tmp_path, name: str, *, passphrase: str = ""):
    from core.session_portability import api

    out = tmp_path / name
    api.export_session(SESSION, out, passphrase=passphrase)
    return out


def test_encrypted_roundtrip(tmp_path):
    from core.session_portability import api

    out = _export(tmp_path, "enc.voolsession", passphrase=PASSPHRASE)

    # The file on disk is an envelope, not a readable zip.
    assert not zipfile.is_zipfile(out)
    envelope = json.loads(out.read_text())
    assert envelope["format"].startswith("vool.session_bundle")
    assert envelope["cipher"] == "AES-256-GCM"
    assert envelope["kdf"]["name"] == "PBKDF2-HMAC-SHA256"

    summary = api.inspect_bundle(out, passphrase=PASSPHRASE)
    assert summary["session"]["session_id"] == SESSION
    assert summary["encrypted"] is True


def test_wrong_passphrase_is_typed_and_reads_nothing(tmp_path):
    from core.session_portability import api

    out = _export(tmp_path, "enc.voolsession", passphrase=PASSPHRASE)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(out, passphrase=WRONG_PASSPHRASE)
    assert err.value.code == "BUNDLE_DECRYPTION_FAILED"

    with pytest.raises(api.PortabilityRefused) as err:
        api.load_payload(out, passphrase=WRONG_PASSPHRASE)
    assert err.value.code == "BUNDLE_DECRYPTION_FAILED"


def test_missing_passphrase_on_encrypted_bundle_refuses(tmp_path):
    from core.session_portability import api

    out = _export(tmp_path, "enc.voolsession", passphrase=PASSPHRASE)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(out)
    assert err.value.code == "BUNDLE_DECRYPTION_FAILED"


def test_tampered_payload_member_is_refused_and_named(tmp_path):
    from core.session_portability import api

    src = _export(tmp_path, "plain.voolsession")
    out = tmp_path / "tampered.voolsession"
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        members = {name: zf.read(name) for name in names}
    payload = json.loads(members["bundle.json"])
    payload["turns"][0]["assistant"] = payload["turns"][0]["assistant"] + " (edited in flight)"
    members["bundle.json"] = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(out)
    assert err.value.code == "BUNDLE_TAMPERED"
    assert "bundle.json" in str(err.value)


def test_tampered_attachment_member_is_refused(tmp_path):
    from core.session_portability import api

    support.seed_attachment()
    src = _export(tmp_path, "plain.voolsession")
    out = tmp_path / "tampered.voolsession"
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        members = {name: zf.read(name) for name in names}
    attachment_names = [name for name in names if name.startswith("attachments/")]
    assert attachment_names, "the seeded attachment must ship as an embedded member"
    victim = attachment_names[0]
    members[victim] = members[victim] + b"extra bytes"
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(out)
    assert err.value.code == "BUNDLE_TAMPERED"
    assert victim in str(err.value)


def test_manifest_spliced_from_another_bundle_is_refused(tmp_path):
    """Member hashes are verified against the manifest, so a manifest carried over from a
    DIFFERENT bundle cannot cover an edited payload: the entry hash mismatches. (A fully
    self-consistent rewrite by a keyless attacker is outside hash-only tamper rejection; the
    passphrase envelope closes that case cryptographically — see the encryption tests.)"""
    from core.session_portability import api

    src = _export(tmp_path, "plain.voolsession")
    # A second bundle with DIFFERENT content (one more turn), so its manifest genuinely differs.
    support.seed_turns(turns=[("a third question", "a third answer")])
    other = _export(tmp_path, "other.voolsession")
    out = tmp_path / "spliced.voolsession"
    with zipfile.ZipFile(src) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    with zipfile.ZipFile(other) as zf:
        foreign_manifest = zf.read("manifest.json")
    members["manifest.json"] = foreign_manifest
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(out)
    assert err.value.code == "BUNDLE_TAMPERED"


def test_malformed_file_refuses_cleanly(tmp_path):
    from core.session_portability import api

    garbage = tmp_path / "garbage.voolsession"
    garbage.write_bytes(b"not a bundle at all")
    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(garbage)
    assert err.value.code == "BUNDLE_MALFORMED"

    truncated = tmp_path / "truncated.voolsession"
    src = _export(tmp_path, "plain.voolsession")
    shutil.copyfile(src, truncated)
    truncated.write_bytes(truncated.read_bytes()[:200])
    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(truncated)
    assert err.value.code in {"BUNDLE_MALFORMED", "BUNDLE_TAMPERED"}


def test_plaintext_bundle_rejected_as_envelope_and_vice_versa(tmp_path):
    from core.session_portability import api

    plain = _export(tmp_path, "plain.voolsession")
    enc = _export(tmp_path, "enc.voolsession", passphrase=PASSPHRASE)

    # A plaintext zip opened with a passphrase must not pretend the passphrase did anything.
    summary = api.inspect_bundle(plain, passphrase=PASSPHRASE)
    assert summary["encrypted"] is False

    # An envelope read without its passphrase is a typed decryption refusal, never a
    # half-read: the file is recognized as an envelope and refuses closed.
    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(enc)
    assert err.value.code == "BUNDLE_DECRYPTION_FAILED"

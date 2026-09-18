"""Authentic bundle identity: every bundle is Ed25519-signed by VOOL's node-signing authority.

Laws:
- the signature covers the manifest bytes, and the manifest covers every member — so an
  attacker who edits content AND recomputes hashes still cannot produce a TRUSTED bundle
  (their re-signed bundle carries an unknown signer fingerprint);
- invalid signatures fail BEFORE any import write;
- the node's own key is trusted; unknown signers need explicit operator confirmation and
  stay marked foreign; fingerprints added to the home's trusted registry import normally;
- encryption and signing are independent: an encrypted bundle is still signed inside.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from tests.session_portability import support
from tests.session_portability.support import SESSION


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.seed_tool_receipt()


def _members(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_every_export_is_signed_by_the_node_key(tmp_path):
    from core.session_portability import api
    from network import signer

    out = tmp_path / "s.voolsession"
    receipt = api.export_session(SESSION, out)

    members = _members(out)
    assert "signature.json" in members
    sig = json.loads(members["signature.json"])
    assert sig["algorithm"] == "ed25519"
    assert sig["signer_public_key"] == signer.get_local_peer_id()
    # The signature really verifies over the manifest bytes with the node key.
    assert signer.verify(members["manifest.json"], sig["signature"], sig["signer_public_key"])

    summary = api.inspect_bundle(out)
    assert summary["signature"]["trusted"] is True
    assert summary["signature"]["origin"] == "self"
    assert receipt["trust"]["origin"] == "self"


def test_recomputed_hashes_cannot_produce_a_trusted_bundle(tmp_path):
    """The hash-only forgery gap is closed: edit content, recompute every hash and the manifest
    digest — the stale signature no longer covers the forged manifest, and a RE-SIGNED bundle
    carries the attacker's key (see the unknown-signer test)."""
    from core.session_portability import api

    src = tmp_path / "plain.voolsession"
    api.export_session(SESSION, src)

    members = _members(src)
    import hashlib

    payload = json.loads(members["bundle.json"])
    payload["turns"][0]["assistant"] = "the attacker's sentence, hashes and all"
    members["bundle.json"] = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    manifest = json.loads(members["manifest.json"])
    entries = dict(manifest["entries"])
    entries["bundle.json"] = hashlib.sha256(members["bundle.json"]).hexdigest()
    manifest["entries"] = entries
    digest_lines = "\n".join(f"{p} {entries[p]}" for p in sorted(entries))
    manifest["bundle_digest"] = hashlib.sha256(digest_lines.encode()).hexdigest()
    members["manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()

    forged = tmp_path / "forged.voolsession"
    with zipfile.ZipFile(forged, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(forged)
    assert err.value.code == "BUNDLE_SIGNATURE_INVALID"


def test_signature_tamper_fails_before_any_write(tmp_path):
    from core.session_portability import api

    src = tmp_path / "plain.voolsession"
    api.export_session(SESSION, src)
    members = _members(src)
    sig = json.loads(members["signature.json"])
    sig["signature"] = ("A" if not sig["signature"].startswith("A") else "B") + sig["signature"][1:]
    members["signature.json"] = json.dumps(sig).encode()

    out = tmp_path / "badsig.voolsession"
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    fresh = tmp_path / "fresh-home"
    with pytest.raises(api.PortabilityRefused) as err:
        api.import_bundle(out, home=fresh)
    assert err.value.code == "BUNDLE_SIGNATURE_INVALID"
    # Nothing was written: no ledger row, no transcript row.
    from core.session_portability.importer import import_ledger_state

    assert import_ledger_state(home=fresh) == {}


def test_unknown_signer_needs_explicit_confirmation_and_stays_foreign(tmp_path):
    """A bundle signed by a foreign Ed25519 key is refused unless the operator explicitly
    confirms; a confirmed import stays marked foreign/untrusted in the durable ledger."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.session_portability import api, signing

    src = tmp_path / "plain.voolsession"
    api.export_session(SESSION, src)
    members = _members(src)

    foreign = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    foreign_pub = foreign.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    manifest = members["manifest.json"]
    sig_bytes = foreign.sign(manifest)
    sig = {
        "format": signing.SIGNATURE_FORMAT,
        "algorithm": "ed25519",
        "signer_public_key": foreign_pub,
        "signer_fingerprint": signing.fingerprint_of(foreign_pub),
        "signed": "manifest.sha256",
        "manifest_sha256": __import__("hashlib").sha256(manifest).hexdigest(),
        "signature": __import__("base64").b64encode(sig_bytes).decode(),
    }
    members["signature.json"] = json.dumps(sig, sort_keys=True).encode()
    foreign_bundle = tmp_path / "foreign.voolsession"
    with zipfile.ZipFile(foreign_bundle, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    fresh = tmp_path / "fresh-home"
    with pytest.raises(api.PortabilityRefused) as err:
        api.import_bundle(foreign_bundle, home=fresh)
    assert err.value.code == "BUNDLE_UNTRUSTED_SIGNER"

    receipt = api.import_bundle(foreign_bundle, home=fresh, confirm_untrusted=True)
    assert receipt["trust"]["origin"] == "foreign"
    assert receipt["trust"]["trusted"] is False

    from core.session_portability.importer import import_ledger_state

    ledger = import_ledger_state(home=fresh)
    row = ledger[receipt["bundle_id"]]
    assert row["trust"] == "foreign"
    assert row["signer_fingerprint"] == signing.fingerprint_of(foreign_pub)


def test_trusted_registry_fingerprint_imports_normally(tmp_path, tmp_path_factory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.session_portability import api, signing

    src = tmp_path / "plain.voolsession"
    api.export_session(SESSION, src)
    members = _members(src)

    known = Ed25519PrivateKey.generate()
    known_pub = known.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    manifest = members["manifest.json"]
    import base64 as b64
    import hashlib

    sig = {
        "format": signing.SIGNATURE_FORMAT,
        "algorithm": "ed25519",
        "signer_public_key": known_pub,
        "signer_fingerprint": signing.fingerprint_of(known_pub),
        "signed": "manifest.sha256",
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "signature": b64.b64encode(known.sign(manifest)).decode(),
    }
    members["signature.json"] = json.dumps(sig, sort_keys=True).encode()
    known_bundle = tmp_path / "known.voolsession"
    with zipfile.ZipFile(known_bundle, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    # The trust registry belongs to the home PERFORMING the import: register the
    # fingerprint in the target home's registry.
    fresh = tmp_path / "fresh-home"
    from core.session_portability.paths import scoped_home

    with scoped_home(fresh):
        signing.add_trusted_fingerprint(signing.fingerprint_of(known_pub))

    receipt = api.import_bundle(known_bundle, home=fresh)
    assert receipt["trust"]["origin"] == "trusted"
    assert receipt["trust"]["trusted"] is True


def test_encrypted_bundle_is_still_signed(tmp_path):
    from core.session_portability import api
    from network import signer

    out = tmp_path / "enc.voolsession"
    api.export_session(SESSION, out, passphrase="a long passphrase")

    summary = api.inspect_bundle(out, passphrase="a long passphrase")
    assert summary["signature"]["origin"] == "self"
    assert summary["signature"]["trusted"] is True
    assert summary["signature"]["signer_public_key"] == signer.get_local_peer_id()


def test_unsigned_legacy_bundle_refuses_closed(tmp_path):
    from core.session_portability import api

    src = tmp_path / "plain.voolsession"
    api.export_session(SESSION, src)
    members = _members(src)
    members.pop("signature.json")

    legacy = tmp_path / "legacy.voolsession"
    with zipfile.ZipFile(legacy, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(legacy)
    assert err.value.code == "BUNDLE_UNSIGNED"

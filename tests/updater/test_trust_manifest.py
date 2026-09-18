"""Trust anchors + signed-manifest verification — the fail-closed root of the updater.

Every defense named in the mission lands here first: forged manifests, unknown or
rotated-out keys, unsigned documents, tampered bodies, future-dated manifests, malformed
schemas. The verifier must refuse all of them with a typed reason, and must refuse
EVERYTHING when no publisher key is pinned (fail-closed, the NIA-026 decision applied).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.updater.manifest import (
    ArtifactEntry,
    ManifestReason,
    parse_and_verify_manifest,
)
from core.updater.trust import TrustedPublishers

from .helpers import build_manifest, canonical_manifest_bytes, generate_publisher_keypair, manifest_bytes, sign_bytes

NOW = datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def keys():
    private_hex, public_hex = generate_publisher_keypair()
    return {"release-2026-01": public_hex}, private_hex


@pytest.fixture()
def trust(keys):
    pinned, _ = keys
    return TrustedPublishers(pinned_keys=pinned)


def _valid_manifest(keys) -> dict:
    _pinned, private_hex = keys
    manifest, _ = build_manifest(private_hex=private_hex)
    return manifest


class TestTrustedPublishers:
    def test_empty_trust_is_fail_closed(self):
        publishers = TrustedPublishers(pinned_keys={})
        assert not publishers.is_configured
        assert not publishers.is_known_key("release-2026-01")

    def test_env_override_parses_keyid_hex_pairs(self, monkeypatch):
        _, public_hex = generate_publisher_keypair()
        monkeypatch.setenv("VOOL_UPDATE_PUBLISHER_KEYS", f"release-x:{public_hex}")
        publishers = TrustedPublishers.from_env()
        assert publishers.is_known_key("release-x")

    def test_env_override_rejects_malformed_entries(self, monkeypatch):
        monkeypatch.setenv("VOOL_UPDATE_PUBLISHER_KEYS", "not-a-pair")
        assert not TrustedPublishers.from_env().is_configured

    def test_config_file_pins_keys(self, tmp_path):
        _, public_hex = generate_publisher_keypair()
        cfg = tmp_path / "trusted_publisher_keys.json"
        cfg.write_text(json.dumps({"release-2026-01": public_hex}))
        publishers = TrustedPublishers.load(config_path=cfg)
        assert publishers.is_known_key("release-2026-01")


class TestManifestVerification:
    def test_valid_signed_manifest_verifies(self, keys, trust):
        result = parse_and_verify_manifest(manifest_bytes(_valid_manifest(keys)), trust, now=NOW)
        assert result.ok, result.reason
        assert result.manifest is not None
        assert result.manifest.version == "0.6.0"
        assert result.manifest.sequence == 47

    def test_missing_signature_is_refused(self, keys, trust):
        manifest, _ = build_manifest()  # unsigned
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.UNSIGNED

    def test_no_trusted_publisher_refuses_everything(self, keys):
        result = parse_and_verify_manifest(
            manifest_bytes(_valid_manifest(keys)), TrustedPublishers(pinned_keys={}), now=NOW
        )
        assert not result.ok
        assert result.reason == ManifestReason.NO_TRUSTED_PUBLISHER

    def test_unknown_key_id_is_refused(self, keys, trust):
        _pinned, private_hex = keys
        manifest, _ = build_manifest(private_hex=private_hex, key_id="release-unknown")
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.UNKNOWN_SIGNING_KEY

    def test_tampered_body_fails_signature(self, keys, trust):
        manifest = _valid_manifest(keys)
        manifest["version"] = "99.0.0"  # attacker edits AFTER signing
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.SIGNATURE_MISMATCH

    def test_tampered_artifact_entry_fails_signature(self, keys, trust):
        manifest = _valid_manifest(keys)
        manifest["artifacts"]["macos-arm64"]["sha256"] = "0" * 64
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.SIGNATURE_MISMATCH

    def test_rotated_out_key_no_longer_verifies(self, keys):
        pinned, private_hex = keys
        old_manifest, _ = build_manifest(private_hex=private_hex, key_id="release-2025-old")
        rotated = TrustedPublishers(pinned_keys=pinned)  # only the new key id is pinned
        result = parse_and_verify_manifest(manifest_bytes(old_manifest), rotated, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.UNKNOWN_SIGNING_KEY

    def test_malformed_json_is_typed_not_raised(self, trust):
        result = parse_and_verify_manifest(b"{not json", trust, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.MALFORMED

    def test_wrong_schema_refused(self, keys, trust):
        _pinned, private_hex = keys
        manifest, _ = build_manifest(private_hex=private_hex)
        manifest["schema"] = "vool.update.manifest.v999"
        # resign so only the schema check can refuse it
        manifest["signature"] = {
            "key_id": "release-2026-01",
            "sig": sign_bytes(private_hex, canonical_manifest_bytes(manifest)),
        }
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert result.reason == ManifestReason.UNSUPPORTED_SCHEMA

    def test_unknown_channel_refused(self, keys, trust):
        _pinned, private_hex = keys
        manifest, _ = build_manifest(private_hex=private_hex, channel="nightly")
        manifest["signature"] = {
            "key_id": "release-2026-01",
            "sig": sign_bytes(private_hex, canonical_manifest_bytes(manifest)),
        }
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert result.reason == ManifestReason.INVALID

    def test_future_dated_manifest_refused(self, keys, trust):
        _pinned, private_hex = keys
        future = (NOW + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest, _ = build_manifest(private_hex=private_hex, published_at=future)
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert not result.ok
        assert result.reason == ManifestReason.PUBLISHED_IN_FUTURE

    def test_reasonable_clock_skew_is_accepted(self, keys, trust):
        _pinned, private_hex = keys
        soon = (NOW + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest, _ = build_manifest(private_hex=private_hex, published_at=soon)
        assert parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW).ok

    def test_bad_version_string_refused(self, keys, trust):
        _pinned, private_hex = keys
        manifest, _ = build_manifest(private_hex=private_hex, version="not.a.version")
        manifest["signature"] = {
            "key_id": "release-2026-01",
            "sig": sign_bytes(private_hex, canonical_manifest_bytes(manifest)),
        }
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert result.reason == ManifestReason.INVALID

    def test_bad_artifact_fields_refused(self, keys, trust):
        _pinned, private_hex = keys
        manifest, _ = build_manifest(private_hex=private_hex)
        manifest["artifacts"]["macos-arm64"]["size"] = -5
        manifest["signature"] = {
            "key_id": "release-2026-01",
            "sig": sign_bytes(private_hex, canonical_manifest_bytes(manifest)),
        }
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert result.reason == ManifestReason.INVALID

    def test_garbage_signature_encoding_is_signature_mismatch(self, keys, trust):
        manifest = _valid_manifest(keys)
        manifest["signature"]["sig"] = base64.b64encode(b"\x00garbage").decode()
        result = parse_and_verify_manifest(manifest_bytes(manifest), trust, now=NOW)
        assert not result.ok
        assert result.reason in (ManifestReason.SIGNATURE_MISMATCH, ManifestReason.INVALID)


class TestArtifactEntry:
    def test_entry_roundtrip(self, keys):
        manifest = _valid_manifest(keys)
        entry = manifest["artifacts"]["macos-arm64"]
        parsed = ArtifactEntry.from_dict("macos-arm64", entry)
        assert parsed.platform == "macos-arm64"
        assert parsed.size == entry["size"]
        assert parsed.sha256 == entry["sha256"]

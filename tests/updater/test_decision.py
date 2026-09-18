"""Decision authority — channel/version/replay/downgrade/platform gate (pure, no I/O).

Mission rules pinned here:
  * stable/beta channels; the manifest channel must match the configured channel;
  * NO downgrade unless a typed authorization explicitly says so;
  * replayed manifests (sequence <= the persisted high-water) are refused — this is also
    the manifest-rollback defense (an older SIGNED manifest still has a lower sequence);
  * a platform with no artifact is reported accurately, never skipped silently;
  * a platform whose installer does not exist yet is reported as such (Windows/Linux).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.updater.decision import (
    DowngradeAuthorization,
    UpdateDecisionReason,
    decide_update,
)
from core.updater.manifest import VerifiedManifest, parse_and_verify_manifest

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes

NOW = datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def signed():
    private_hex, public_hex = generate_publisher_keypair()
    return private_hex, {"release-2026-01": public_hex}


def _manifest(keys, *, version="0.6.0", channel="stable", sequence=47, platforms=("macos-arm64",), minimum_compatible="0.4.0"):
    private_hex, _ = keys
    artifacts = {p: f"BLOB-{p}-{version}".encode() for p in platforms}
    manifest, _ = build_manifest(
        version=version,
        channel=channel,
        sequence=sequence,
        artifacts=artifacts,
        private_hex=private_hex,
        minimum_compatible=minimum_compatible,
    )
    result = parse_and_verify_manifest(manifest_bytes(manifest), _trust(keys), now=NOW)
    assert result.ok, result.reason
    return result.manifest


def _trust(keys):
    from core.updater.trust import TrustedPublishers

    _, pinned = keys
    return TrustedPublishers(pinned_keys=pinned)


class TestHappyPath:
    def test_update_available_for_matching_platform(self, signed):
        decision = decide_update(
            _manifest(signed),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 40, "version": "0.5.9"},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE
        assert decision.artifact is not None
        assert decision.artifact.platform == "macos-arm64"
        assert "0.6.0" in decision.plain_message

    def test_equal_version_is_up_to_date(self, signed):
        decision = decide_update(
            _manifest(signed, version="0.5.0"),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.UP_TO_DATE

    def test_beta_channel_accepts_beta_release(self, signed):
        decision = decide_update(
            _manifest(signed, version="0.6.0-beta.2", channel="beta", sequence=12),
            installed_version="0.6.0-beta.1",
            channel="beta",
            platform_key="macos-arm64",
            high_water={"sequence": 11},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE

    def test_installed_below_minimum_compatible_still_offers(self, signed):
        decision = decide_update(
            _manifest(signed),
            installed_version="0.3.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 0},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE


class TestDowngrade:
    def test_downgrade_is_refused_without_authorization(self, signed):
        decision = decide_update(
            _manifest(signed, version="0.4.0", sequence=50),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.DOWNGRADE_REFUSED
        assert "older" in decision.plain_message.lower()

    def test_explicit_authorization_allows_downgrade(self, signed):
        auth = DowngradeAuthorization(target_version="0.4.0", reason="operator rollback drill")
        decision = decide_update(
            _manifest(signed, version="0.4.0", sequence=50),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
            downgrade_authorization=auth,
        )
        assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE
        assert decision.authorized_downgrade

    def test_authorization_for_a_different_version_does_not_apply(self, signed):
        auth = DowngradeAuthorization(target_version="0.4.1", reason="wrong target")
        decision = decide_update(
            _manifest(signed, version="0.4.0", sequence=50),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
            downgrade_authorization=auth,
        )
        assert decision.reason is UpdateDecisionReason.DOWNGRADE_REFUSED


class TestReplayAndRollback:
    def test_equal_sequence_is_a_replay(self, signed):
        decision = decide_update(
            _manifest(signed, sequence=47),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 47, "version": "0.6.0"},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.REPLAYED_MANIFEST

    def test_older_sequence_is_a_manifest_rollback(self, signed):
        """The hostile-mirror classic: re-serve a VALIDLY SIGNED older manifest."""
        decision = decide_update(
            _manifest(signed, version="0.4.0", sequence=30),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.REPLAYED_MANIFEST

    def test_older_sequence_with_higher_version_is_still_refused(self, signed):
        """Version strings are display; the sequence is the monotonic truth."""
        decision = decide_update(
            _manifest(signed, version="99.0.0", sequence=30),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.REPLAYED_MANIFEST


class TestChannelAndPlatform:
    def test_channel_mismatch_refused(self, signed):
        decision = decide_update(
            _manifest(signed, channel="beta", sequence=60),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 46},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.CHANNEL_MISMATCH

    def test_missing_platform_artifact_reported(self, signed):
        decision = decide_update(
            _manifest(signed, platforms=("windows-x64",)),
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 40},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.NO_ARTIFACT_FOR_PLATFORM
        assert "macos-arm64" in decision.plain_message

    def test_windows_installer_boundary_is_honest(self, signed):
        """windows-x64 artifact exists, but no Windows installer is registered: the
        decision must SAY that, not pretend, not offer."""
        decision = decide_update(
            _manifest(signed, platforms=("windows-x64",)),
            installed_version="0.5.0",
            channel="stable",
            platform_key="windows-x64",
            high_water={"sequence": 40},
            installer_available=False,
        )
        assert decision.reason is UpdateDecisionReason.UNSUPPORTED_INSTALLER_PLATFORM
        assert "windows-x64" in decision.plain_message
        assert "installer" in decision.plain_message.lower()

    def test_linux_installer_boundary_is_honest(self, signed):
        decision = decide_update(
            _manifest(signed, platforms=("linux-x64",)),
            installed_version="0.5.0",
            channel="stable",
            platform_key="linux-x64",
            high_water={"sequence": 40},
            installer_available=False,
        )
        assert decision.reason is UpdateDecisionReason.UNSUPPORTED_INSTALLER_PLATFORM

    def test_installed_below_minimum_compatible_flagged(self, signed):
        decision = decide_update(
            _manifest(signed, minimum_compatible="0.4.0"),
            installed_version="0.3.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 40},
            installer_available=True,
        )
        assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE
        assert decision.below_minimum_compatible


class TestPurity:
    def test_decision_does_not_mutate_high_water(self, signed):
        manifest: VerifiedManifest = _manifest(signed)
        decide_update(
            manifest,
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water={"sequence": 40},
            installer_available=True,
        )
        assert manifest.sequence == 47  # untouched input

"""Decision authority: should THIS install take THIS verified manifest?

Pure function of (verified manifest, installed version, configured channel, platform,
persisted high-water mark, explicit authorizations, installer availability). No I/O, no
state mutation — the caller records the new high-water when a decision is COMMITTED, not
when it is made, so merely looking at a manifest can never poison the replay defense.

Rules, in evaluation order:

  1. channel must match the configured channel exactly (stable follows stable, beta beta);
    2. the manifest's sequence must EXCEED the persisted per-channel high-water — this one
     check defends both replay (re-serving today's manifest) and manifest rollback
     (re-serving an older, still-validly-signed manifest). ONE retry carve-out: a manifest
     whose sequence EQUALS the high-water and whose canonical sha256 matches the recorded
     one is the SAME document (retrying a download that failed), not a replay. A different
     document with an already-seen sequence is refused.
  3. version must be strictly newer unless a typed DowngradeAuthorization naming this
     exact version is supplied (no accidental downgrades, ever);
  4. the artifact for the local platform key must exist — a missing entry is reported
     accurately, never substituted with another platform's artifact;
  5. the platform's installer must actually exist (the Windows/Linux contract boundary:
     the shared manifest can describe those artifacts while this build honestly reports
     it has no installer for them).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.updater.manifest import ArtifactEntry, VerifiedManifest
from core.updater.versions import parse_semver


class UpdateDecisionReason(Enum):
    UPDATE_AVAILABLE = "update_available"
    UP_TO_DATE = "up_to_date"
    CHANNEL_MISMATCH = "channel_mismatch"
    REPLAYED_MANIFEST = "replayed_manifest"
    DOWNGRADE_REFUSED = "downgrade_refused"
    NO_ARTIFACT_FOR_PLATFORM = "no_artifact_for_platform"
    UNSUPPORTED_INSTALLER_PLATFORM = "unsupported_installer_platform"


@dataclass(frozen=True)
class DowngradeAuthorization:
    """The ONLY way a manifest older than the installed version may install. Explicit,
    typed, version-pinned — a blanket 'allow downgrades' switch is exactly the footgun
    this object exists to prevent."""

    target_version: str
    reason: str
    authorized_by: str = "operator"

    def covers(self, version: str) -> bool:
        return str(self.target_version or "") == str(version)


@dataclass(frozen=True)
class UpdateDecision:
    reason: UpdateDecisionReason
    manifest: VerifiedManifest
    artifact: ArtifactEntry | None = None
    authorized_downgrade: bool = False
    below_minimum_compatible: bool = False
    detail: str = ""

    @property
    def should_install(self) -> bool:
        return self.reason is UpdateDecisionReason.UPDATE_AVAILABLE and self.artifact is not None

    @property
    def plain_message(self) -> str:
        target = self.manifest.version
        installed = self.detail or ""
        if self.reason is UpdateDecisionReason.UPDATE_AVAILABLE:
            suffix = " (an older version you asked to go back to)" if self.authorized_downgrade else ""
            return f"A new version of the app is ready: {target}{suffix}. Press Update to install it."
        if self.reason is UpdateDecisionReason.UP_TO_DATE:
            return "You're up to date — no update needed."
        if self.reason is UpdateDecisionReason.CHANNEL_MISMATCH:
            return (
                f"This update is for the {self.manifest.channel} channel, but this app is on the "
                f"{self._configured_channel} channel, so it was ignored."
            )
        if self.reason is UpdateDecisionReason.REPLAYED_MANIFEST:
            return "This update information is older than what this app has already seen, so it was ignored."
        if self.reason is UpdateDecisionReason.DOWNGRADE_REFUSED:
            return (
                f"The offered version {target} is older than the one installed{f' ({installed})' if installed else ''}, "
                "and the app doesn't install older versions unless you explicitly ask for that."
            )
        if self.reason is UpdateDecisionReason.NO_ARTIFACT_FOR_PLATFORM:
            present = ", ".join(sorted(self.manifest.artifacts)) or "none"
            return (
                f"This release has no download for {self._platform_key} (it provides: {present}). "
                "Nothing was installed."
            )
        if self.reason is UpdateDecisionReason.UNSUPPORTED_INSTALLER_PLATFORM:
            return (
                f"An update for {self._platform_key} is published, but this build of the app has no "
                f"{self._platform_key} installer yet — nothing was installed."
            )
        return "No update."

    _configured_channel: str = ""
    _platform_key: str = ""


def decide_update(
    manifest: VerifiedManifest,
    *,
    installed_version: str,
    channel: str,
    platform_key: str,
    high_water: dict | None,
    installer_available: bool,
    downgrade_authorization: DowngradeAuthorization | None = None,
) -> UpdateDecision:
    high_water = dict(high_water or {})
    installed_semver = parse_semver(installed_version)
    candidate_semver = manifest.semver
    below_minimum = False
    try:
        below_minimum = installed_semver < parse_semver(manifest.minimum_compatible)
    except ValueError:
        below_minimum = False

    def outcome(reason: UpdateDecisionReason, artifact: ArtifactEntry | None = None, **extra) -> UpdateDecision:
        return UpdateDecision(
            reason=reason,
            manifest=manifest,
            artifact=artifact,
            below_minimum_compatible=below_minimum,
            detail=installed_version,
            _configured_channel=str(channel),
            _platform_key=str(platform_key),
            **extra,
        )

    if manifest.channel != channel:
        return outcome(UpdateDecisionReason.CHANNEL_MISMATCH)

    seen_sequence = int(high_water.get("sequence") or 0)
    if manifest.sequence == seen_sequence:
        if str(high_water.get("manifest_sha256") or "") != manifest.canonical_sha256:
            return outcome(UpdateDecisionReason.REPLAYED_MANIFEST)
        # Same sequence AND same canonical bytes: a retry of an already-seen document
        # (e.g. the download failed after the check succeeded) — allowed through.
    elif manifest.sequence < seen_sequence:
        return outcome(UpdateDecisionReason.REPLAYED_MANIFEST)

    authorized_downgrade = False
    if candidate_semver == installed_semver:
        return outcome(UpdateDecisionReason.UP_TO_DATE)
    if candidate_semver < installed_semver:
        if downgrade_authorization is None or not downgrade_authorization.covers(manifest.version):
            return outcome(UpdateDecisionReason.DOWNGRADE_REFUSED)
        authorized_downgrade = True

    artifact = manifest.artifact_for(platform_key)
    if artifact is None:
        return outcome(UpdateDecisionReason.NO_ARTIFACT_FOR_PLATFORM)

    if not installer_available:
        return outcome(UpdateDecisionReason.UNSUPPORTED_INSTALLER_PLATFORM)

    return outcome(
        UpdateDecisionReason.UPDATE_AVAILABLE,
        artifact=artifact,
        authorized_downgrade=authorized_downgrade,
    )


__all__ = ["DowngradeAuthorization", "UpdateDecision", "UpdateDecisionReason", "decide_update"]

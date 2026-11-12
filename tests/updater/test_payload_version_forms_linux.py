"""The Linux package gate accepts exactly two VERSION forms: plain and -beta."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from core.updater.decision import UpdateDecisionReason, decide_update
from core.updater.download import StagedDownloader
from core.updater.health import HealthResult
from core.updater.manifest import parse_and_verify_manifest
from core.updater.migrations import MigrationCatalog
from core.updater.platforms import LINUX_BUNDLE_X64, BundleVerification
from core.updater.transaction import InProcessHelper, Step, UpdateFlow
from core.updater.trust import TrustedPublishers
from core.updater.work import WorkCoordinator

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes

SIGNED_VERSION = "0.7.8"
ACCEPTED_FORMS = (SIGNED_VERSION, SIGNED_VERSION + "-beta")
MATCHING_COMMIT = "a" * 40


def package_bytes(version_form: str, source_commit: str) -> bytes:
    """A minimal Linux package payload: top-level VERSION and SOURCE_COMMIT files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("VERSION", version_form)
        archive.writestr("SOURCE_COMMIT", source_commit)
    return buffer.getvalue()


class FakeServer:
    """Serves the payload bytes to the staged downloader, range requests included."""

    def __init__(self, blob: bytes):
        self.blob = blob

    def open(self, url, headers=None, timeout=30.0):
        headers = headers or {}
        rng = headers.get("Range")
        if rng:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = self.blob[start:]
        else:
            body = self.blob

        class _Resp:
            status = 206 if rng else 200
            headers = {"ETag": '"e1"'}

            def __init__(self, payload):
                self._body = payload
                self._offset = 0

            def read(self, n=-1):
                chunk = self._body[self._offset : self._offset + n if n and n > 0 else None]
                self._offset += len(chunk)
                return chunk

            def close(self):
                pass

        return _Resp(body)


class StubInstaller:
    """Platform installer stand-in: preflight must pass its checks and stop at
    verify_bundle, whose sentinel failure is the proof the gate was cleared."""

    name = "stub-linux-bundle-installer"

    def __init__(self):
        self.verified: list[Path] = []

    def verify_bundle(self, staged_bundle: Path, *, require_notarization=None) -> BundleVerification:
        self.verified.append(Path(staged_bundle))
        return BundleVerification(False, detail="sentinel-verify-stop")


def _health_probe(*, timeout, expected_version=None) -> HealthResult:
    return HealthResult(True, "probe not reached before the gate")


class LinuxGateRig:
    """A signed 0.7.8 stable offer for the per-user Linux package, driven end to end:
    real signed manifest and artifact, real staged download, real zip extraction."""

    def __init__(self, tmp_path: Path, *, version_form: str, build_commit: str = "",
                 source_commit: str = MATCHING_COMMIT):
        self.apps_dir = tmp_path / "opt"
        self.app_path = self.apps_dir / "vool"
        self.app_path.mkdir(parents=True)
        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir(parents=True)
        blob = package_bytes(version_form, source_commit)

        private_hex, public_hex = generate_publisher_keypair()
        document, _ = build_manifest(
            version=SIGNED_VERSION,
            channel="stable",
            artifacts={LINUX_BUNDLE_X64: blob},
            private_hex=private_hex,
            build_commit=build_commit,
        )
        self.raw_manifest = manifest_bytes(document)
        self.trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
        verification = parse_and_verify_manifest(self.raw_manifest, self.trust)
        assert verification.ok, verification.reason
        self.manifest = verification.manifest
        self.decision = decide_update(
            self.manifest,
            installed_version="0.7.6",
            channel="stable",
            platform_key=LINUX_BUNDLE_X64,
            high_water={},
            installer_available=True,
        )
        assert self.decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE

        self.installer = StubInstaller()
        self.flow = UpdateFlow(
            data_dir=self.data_dir,
            app_path=self.app_path,
            platform=LINUX_BUNDLE_X64,
            installed_version="0.7.6",
            channel="stable",
            trust=self.trust,
            downloader=StagedDownloader(fetch=FakeServer(blob).open, chunk_size=64),
            coordinator=WorkCoordinator(),
            migration_catalog=MigrationCatalog(),
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=_health_probe,
            installer=self.installer,
        )

    def run(self):
        return self.flow.run(self.raw_manifest, self.manifest, self.decision)

    def stage_leftovers(self) -> list[Path]:
        return sorted(self.apps_dir.glob(".vool-stage-*"))


@pytest.mark.parametrize("version_form", ACCEPTED_FORMS)
def test_signed_forms_pass_the_gate_and_reach_platform_verification(tmp_path, version_form):
    rig = LinuxGateRig(tmp_path, version_form=version_form)
    result = rig.run()
    assert not result.ok  # the stub installer refuses everything after the gate
    assert "sentinel-verify-stop" in result.detail
    assert "incompatible Linux package" not in result.detail
    assert [staged.name for staged in rig.installer.verified], "the flow must reach verify_bundle"


@pytest.mark.parametrize(
    "version_form",
    ["0.7.8-rc1", "0.7.80", "v0.7.8", "0.7.9", "0.7.8-beta2"],
)
def test_any_other_form_is_refused_and_the_stage_is_cleaned_up(tmp_path, version_form):
    rig = LinuxGateRig(tmp_path, version_form=version_form)
    result = rig.run()
    assert not result.ok
    assert result.terminal is Step.ABORTED
    assert "incompatible Linux package" in result.detail
    assert "sentinel-verify-stop" not in result.detail
    assert not rig.installer.verified  # the gate stopped the flow before the installer
    assert not rig.stage_leftovers()


@pytest.mark.parametrize("version_form", ACCEPTED_FORMS)
def test_a_payload_from_another_commit_is_refused_even_for_the_accepted_forms(tmp_path, version_form):
    rig = LinuxGateRig(tmp_path, version_form=version_form,
                       build_commit=MATCHING_COMMIT, source_commit="b" * 40)
    result = rig.run()
    assert not result.ok
    assert result.terminal is Step.ABORTED
    assert "signed build commit" in result.detail
    assert "sentinel-verify-stop" not in result.detail
    assert not rig.installer.verified
    assert not rig.stage_leftovers()

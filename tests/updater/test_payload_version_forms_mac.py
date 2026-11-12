"""The Mac payload version gate accepts exactly two forms and refuses all others.

A release may label its BUILD_MANIFEST.json payload with the signed offer's plain
version ("0.7.8") or the "-beta" form ("0.7.8-beta") — nothing looser. Every test
here drives the FULL real flow on macos-arm64 (real Ed25519-signed manifest, real
zip artifact with a BUILD_MANIFEST.json inside, real download/verify/preflight,
publisher-verified-adhoc trust — the only path where the Mac payload metadata is
checked), so green means the production gate made each call:

  accepted forms install to FINALIZED and swap the marker to "new";
  near-miss forms ("0.7.8-rc1", "0.7.80", "v0.7.8", "0.7.9", "0.7.8-beta2") abort
  with the architecture/version mismatch and leave the old bundle untouched;
  a wrong payload "exact_sha" still aborts on the signed build commit check for
  both accepted version forms.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from core.updater.decision import UpdateDecisionReason, decide_update
from core.updater.download import StagedDownloader
from core.updater.health import HealthResult
from core.updater.macos import MacOSBundleInstaller
from core.updater.manifest import parse_and_verify_manifest
from core.updater.migrations import MigrationCatalog
from core.updater.transaction import InProcessHelper, Step, UpdateFlow
from core.updater.trust import TrustedPublishers
from core.updater.work import WorkCoordinator

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes, sign_bytes

CHANNEL = "stable"
PLATFORM = "macos-arm64"
KEY_ID = "release-2026-01"


class Completed:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class FakeServer:
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


@dataclass
class Harness:
    tmp: Path
    data_dir: Path
    app_path: Path
    server: FakeServer
    coordinator: WorkCoordinator
    catalog: MigrationCatalog
    installer: MacOSBundleInstaller
    relaunch_log: list = field(default_factory=list)
    shutdown_log: list = field(default_factory=list)
    installed_version: str = "0.7.6"
    target_version: str = "0.7.8"
    keys: tuple = ()

    def marker(self) -> str:
        return (self.app_path / "Contents" / "marker.txt").read_text(encoding="utf-8").strip()

    def health(self, *, timeout: float, expected_version: str | None = None) -> HealthResult:
        marker = self.marker()
        reported = self.target_version if marker == "new" else self.installed_version
        ok = (expected_version is None) or reported == expected_version
        return HealthResult(ok, f"marker={marker}", reported)

    def make_flow(self, **overrides) -> UpdateFlow:
        _private_hex, public_hex = self.keys
        params = dict(
            data_dir=self.data_dir,
            app_path=self.app_path,
            platform=PLATFORM,
            installed_version=self.installed_version,
            channel=CHANNEL,
            trust=TrustedPublishers(pinned_keys={KEY_ID: public_hex}),
            downloader=StagedDownloader(fetch=self.server.open, chunk_size=64),
            coordinator=self.coordinator,
            migration_catalog=self.catalog,
            helper=InProcessHelper(
                shutdown=lambda: self.shutdown_log.append("shutdown"),
                relauncher=lambda path: (self.relaunch_log.append(str(path)), True)[1],
            ),
            health_probe=self.health,
            installer=self.installer,
            notarization_waiver_reason="",
            allow_publisher_verified_adhoc=True,
        )
        params.update(overrides)
        return UpdateFlow(**params)


@pytest.fixture()
def harness(tmp_path: Path):
    data_dir = tmp_path / "data"
    (data_dir / "config").mkdir(parents=True)
    (data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "original"}), encoding="utf-8")

    apps = tmp_path / "Apps"
    app_path = apps / "VOOL.app"
    (app_path / "Contents").mkdir(parents=True)
    (app_path / "Contents" / "marker.txt").write_text("old", encoding="utf-8")

    def fake_run(command):
        if command[0] == "codesign" and "-dv" in command:
            return Completed(0, err="Signature=adhoc")
        if command[0] == "spctl":
            return Completed(1, err="not notarized")
        return Completed(0)

    box = Harness(
        tmp=tmp_path,
        data_dir=data_dir,
        app_path=app_path,
        server=FakeServer(b"placeholder"),
        coordinator=WorkCoordinator(),
        catalog=MigrationCatalog(),  # no migration declared for 0.7.6 -> 0.7.8
        installer=MacOSBundleInstaller(run=fake_run),
    )
    box.keys = generate_publisher_keypair()
    return box


def _payload_zip(payload_version: str, exact_sha: str | None = None) -> bytes:
    """Build the real artifact: a bundle whose BUILD_MANIFEST.json names the payload."""
    build_manifest_document = {"arch": "arm64", "version": payload_version}
    if exact_sha is not None:
        build_manifest_document["exact_sha"] = exact_sha
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("VOOL.app/Contents/marker.txt", "new")
        zipped.writestr(
            "VOOL.app/Contents/Resources/BUILD_MANIFEST.json",
            json.dumps(build_manifest_document),
        )
    return archive.getvalue()


def _signed_offer(harness: Harness, blob: bytes, build_commit: str = ""):
    """Sign a manifest around the new blob and verify it with the pinned key."""
    private_hex, public_hex = harness.keys
    document, _ = build_manifest(
        version=harness.target_version,
        channel=CHANNEL,
        artifacts={PLATFORM: blob},
        build_commit=build_commit,
    )
    document["artifacts"][PLATFORM]["signature"] = sign_bytes(private_hex, blob)
    unsigned = {k: v for k, v in document.items() if k != "signature"}
    document["signature"] = {
        "key_id": KEY_ID,
        "sig": sign_bytes(
            private_hex,
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ),
    }
    raw = manifest_bytes(document)
    verification = parse_and_verify_manifest(raw, TrustedPublishers(pinned_keys={KEY_ID: public_hex}))
    assert verification.ok, verification.reason
    decision = decide_update(
        verification.manifest,
        installed_version=harness.installed_version,
        channel=CHANNEL,
        platform_key=PLATFORM,
        high_water={},
        installer_available=True,
    )
    assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE
    return raw, verification.manifest, decision


class TestAcceptedForms:
    @pytest.mark.parametrize("payload_version", ["0.7.8", "0.7.8-beta"])
    def test_the_plain_and_beta_forms_install_and_finalize(self, harness, payload_version):
        blob = _payload_zip(payload_version)
        harness.server.blob = blob
        raw, manifest, decision = _signed_offer(harness, blob)
        result = harness.make_flow().run(raw, manifest, decision)
        assert result.ok, result.detail
        assert result.terminal is Step.FINALIZED
        assert harness.marker() == "new"


class TestRefusedForms:
    @pytest.mark.parametrize(
        "payload_version",
        ["0.7.8-rc1", "0.7.80", "v0.7.8", "0.7.9", "0.7.8-beta2"],
    )
    def test_any_other_form_aborts_without_touching_the_installed_app(self, harness, payload_version):
        blob = _payload_zip(payload_version)
        harness.server.blob = blob
        raw, manifest, decision = _signed_offer(harness, blob)
        result = harness.make_flow().run(raw, manifest, decision)
        assert not result.ok
        assert result.terminal is Step.ABORTED
        assert "architecture/version" in result.detail
        assert harness.marker() == "old"
        assert not harness.shutdown_log
        assert not harness.relaunch_log


class TestBuildCommitStillPinned:
    @pytest.mark.parametrize("payload_version", ["0.7.8", "0.7.8-beta"])
    def test_a_wrong_exact_sha_is_refused_even_for_an_accepted_version_form(
        self, harness, payload_version
    ):
        blob = _payload_zip(payload_version, exact_sha="b" * 40)
        harness.server.blob = blob
        raw, manifest, decision = _signed_offer(harness, blob, build_commit="a" * 40)
        result = harness.make_flow().run(raw, manifest, decision)
        assert not result.ok
        assert result.terminal is Step.ABORTED
        assert "signed build commit" in result.detail
        assert harness.marker() == "old"

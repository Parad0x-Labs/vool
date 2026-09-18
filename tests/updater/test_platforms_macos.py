"""Platform registry boundary + macOS codesign/notarization/swap mechanics.

Codesign checks run against BOTH a fake command runner (typed outcomes) and one REAL
ad-hoc-signed temp bundle (the actual `codesign` binary), so the verifier is proven
against the real tool, not only against its mock. The notarization posture test pins
the production default: an un-notarized bundle is refused.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from core.updater import platforms
from core.updater.macos import MacOSBundleInstaller
from core.updater.platforms import installer_for, platform_key, supported_platforms


class Completed:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class TestPlatformRegistry:
    def test_platform_key_normalizes(self):
        assert platform_key("darwin", "arm64") == "macos-arm64"
        assert platform_key("darwin", "x86_64") == "macos-x64"
        assert platform_key("win32", "AMD64") == "windows-x64"
        assert platform_key("linux", "aarch64") == "linux-arm64"

    def test_this_machine_has_a_key(self):
        key = platform_key()
        assert key.startswith(("macos", "windows", "linux"))

    def test_only_macos_installers_exist_today(self):
        assert "macos-arm64" in supported_platforms()
        assert installer_for("macos-arm64") is not None

    def test_windows_installer_boundary_is_honest(self):
        """The contract boundary: no placeholder, no pretend — None means None."""
        assert installer_for("windows-x64") is None
        assert installer_for("linux-x64") is None


class TestCodeSignVerificationFake:
    def test_codesign_failure_refuses(self, tmp_path):
        installer = MacOSBundleInstaller(run=lambda cmd: Completed(1, "", "code object is not signed at all"))
        result = installer.verify_bundle(tmp_path, require_notarization=False)
        assert not result.ok
        assert not result.codesigned
        assert "codesign" in result.detail

    def test_codesign_ok_without_notarization_requirement(self, tmp_path):
        installer = MacOSBundleInstaller(run=lambda cmd: Completed(0, "", ""))
        result = installer.verify_bundle(tmp_path, require_notarization=False)
        assert result.ok
        assert result.codesigned

    def test_default_posture_requires_notarization(self, tmp_path):
        def run(cmd: list[str]) -> Completed:
            if cmd[0] == "codesign":
                return Completed(0, "", "")
            if cmd[0] == "spctl":
                return Completed(3, "", "rejected (the code is valid but does not seem to be notarized)")
            raise AssertionError(f"unexpected command {cmd}")

        installer = MacOSBundleInstaller(run=run)
        result = installer.verify_bundle(tmp_path)  # default: require_notarization=True
        assert not result.ok
        assert result.codesigned
        assert not result.notarized
        assert "notarized" in result.detail

    def test_notarized_bundle_passes_default_posture(self, tmp_path):
        def run(cmd: list[str]) -> Completed:
            if cmd[0] == "codesign":
                return Completed(0, "", "")
            if cmd[0] == "spctl":
                return Completed(0, "accepted\nsource=Notarized Developer ID", "")
            raise AssertionError(f"unexpected command {cmd}")

        installer = MacOSBundleInstaller(run=run)
        result = installer.verify_bundle(tmp_path)
        assert result.ok
        assert result.notarized

    def test_pinned_authority_mismatch_refuses(self, tmp_path):
        def run(cmd: list[str]) -> Completed:
            if cmd[:2] == ["codesign", "-dv"]:
                return Completed(0, "", "Authority=Someone Else\nAuthority=Apple Root CA")
            return Completed(0, "", "")

        installer = MacOSBundleInstaller(run=run, expected_authority="Developer ID Application: VOOL")
        result = installer.verify_bundle(tmp_path, require_notarization=False)
        assert not result.ok
        assert "unexpected authority" in result.detail

    def test_missing_bundle_dir_refused(self, tmp_path):
        installer = MacOSBundleInstaller()
        result = installer.verify_bundle(tmp_path / "nope.app")
        assert not result.ok


@pytest.mark.skipif(platform_key().split("-")[0] != "macos", reason="real codesign exists only on macOS")
class TestRealCodeSign:
    @pytest.fixture()
    def signed_bundle(self, tmp_path) -> Path:
        bundle = tmp_path / "Fake.app"
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        exe = bundle / "Contents" / "MacOS" / "Fake"
        exe.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
        exe.chmod(0o755)
        (bundle / "Contents" / "Info.plist").write_text(
            "<?xml version='1.0'?><plist version='1.0'><dict><key>CFBundleName</key>"
            "<string>Fake</string></dict></plist>",
            encoding="utf-8",
        )
        result = subprocess.run(["codesign", "-s", "-", str(bundle)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr  # ad-hoc sign must succeed for the fixture
        return bundle

    def test_real_adhoc_signed_bundle_passes_codesign(self, signed_bundle, tmp_path):
        installer = MacOSBundleInstaller()
        result = installer.verify_bundle(signed_bundle, require_notarization=False)
        assert result.ok
        assert result.codesigned

    def test_production_default_refuses_un_notarized_bundle(self, signed_bundle):
        """The load-bearing posture pin: ad-hoc signed ⇒ Gatekeeper refuses ⇒ so do we."""
        installer = MacOSBundleInstaller()
        result = installer.verify_bundle(signed_bundle)  # default requires notarization
        assert not result.ok
        assert not result.notarized

    def test_tampered_bundle_fails_codesign(self, signed_bundle):
        (signed_bundle / "Contents" / "MacOS" / "Fake").write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
        installer = MacOSBundleInstaller()
        result = installer.verify_bundle(signed_bundle, require_notarization=False)
        assert not result.ok


class TestAtomicSwap:
    def _make(self, root: Path, name: str, marker: str) -> Path:
        app = root / name
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "marker.txt").write_text(marker, encoding="utf-8")
        return app

    def test_swap_moves_old_aside_and_new_in(self, tmp_path):
        installer = MacOSBundleInstaller()
        target = self._make(tmp_path, "VOOL.app", "old")
        staged = self._make(tmp_path, "staged-bundle", "new")
        outcome = installer.atomic_swap(staged, target, txid="tx1")
        assert outcome.ok
        assert (target / "Contents" / "marker.txt").read_text() == "new"
        assert outcome.prior_path is not None
        assert (outcome.prior_path / "Contents" / "marker.txt").read_text() == "old"

    def test_failed_second_rename_undoes_the_first(self, tmp_path, monkeypatch):
        import os

        import core.updater.macos as macos_module

        installer = MacOSBundleInstaller()
        target = self._make(tmp_path, "VOOL.app", "old")
        staged = self._make(tmp_path, "staged-bundle", "new")
        real_rename = os.rename
        calls: list[tuple[str, str]] = []

        def flaky_rename(src, dst):
            calls.append((str(src), str(dst)))
            if str(dst).endswith("VOOL.app") and not any(c[1].endswith("VOOL.app") for c in calls[:-1]):
                raise OSError("disk full")  # the into-place rename fails ONCE; the undo may proceed
            return real_rename(src, dst)

        monkeypatch.setattr(macos_module.os, "rename", flaky_rename)
        outcome = installer.atomic_swap(staged, target, txid="tx2")
        assert not outcome.ok
        # target was restored: the old marker is back and runnable
        assert (target / "Contents" / "marker.txt").read_text() == "old"

    def test_cross_volume_swap_refused_not_copied(self, tmp_path, monkeypatch):
        installer = MacOSBundleInstaller()
        target = self._make(tmp_path, "VOOL.app", "old")
        staged = self._make(tmp_path, "staged-bundle", "new")
        monkeypatch.setattr(
            Path, "stat", lambda self, *a, **k: _FakeStat(1 if self.name == "staged-bundle" else 2), raising=False
        )
        outcome = installer.atomic_swap(staged, target, txid="tx3")
        assert not outcome.ok
        assert "different volume" in outcome.detail

    def test_duplicate_prior_for_same_txid_refused(self, tmp_path):
        installer = MacOSBundleInstaller()
        target = self._make(tmp_path, "VOOL.app", "old")
        staged = self._make(tmp_path, "staged-bundle", "new")
        prior = tmp_path / ".VOOL.app.prior-tx4"
        prior.mkdir()
        outcome = installer.atomic_swap(staged, target, txid="tx4")
        assert not outcome.ok
        assert "prior already exists" in outcome.detail

    def test_restore_prior_and_pruning(self, tmp_path):
        installer = MacOSBundleInstaller()
        target = self._make(tmp_path, "VOOL.app", "current")
        old1 = tmp_path / ".VOOL.app.prior-a"
        old2 = tmp_path / ".VOOL.app.prior-b"
        old3 = tmp_path / ".VOOL.app.prior-c"
        for path, marker in ((old1, "one"), (old2, "two"), (old3, "three")):
            (path / "Contents").mkdir(parents=True)
            (path / "Contents" / "marker.txt").write_text(marker, encoding="utf-8")
        outcome = installer.restore_prior(target, old2, txid="txr")
        assert outcome.ok
        assert (target / "Contents" / "marker.txt").read_text() == "two"
        removed = installer.prune_priors(target, keep=1)
        assert len(removed) == 1
        remaining = [p.name for p in tmp_path.glob(".VOOL.app.prior-*")]
        assert remaining == [".VOOL.app.prior-c"]


class _FakeStat:
    def __init__(self, st_dev: int):
        self.st_dev = st_dev


class TestUpdateFeedFollowsTheBundleNotTheProcess:
    """The update feed must be chosen by what the bundle IS, not by what the process runs as.

    `os.uname().machine` reports "x86_64" inside a Rosetta-translated process. An arm64 install
    whose launcher was translated would therefore have asked for the Intel feed and swapped an
    Intel bundle over itself -- the same architecture-selection defect as the launch contract,
    one layer up, and the one that would have shipped an Intel app to an Apple Silicon user.
    """

    def _bundle(self, tmp_path, arch: str):
        res = tmp_path / "VOOL.app" / "Contents" / "Resources"
        (res / "python" / "bin").mkdir(parents=True)
        (res / "BUILD_MANIFEST.json").write_text(
            json.dumps({"schema": "vool-build-manifest/1", "platform": "macos", "arch": arch}),
            encoding="utf-8",
        )
        exe = res / "python" / "bin" / "python3"
        exe.write_text("", encoding="utf-8")
        return exe

    def test_a_translated_process_still_selects_the_bundles_own_architecture(self, tmp_path, monkeypatch):
        exe = self._bundle(tmp_path, "arm64")
        monkeypatch.setattr(platforms.sys, "executable", str(exe))
        # what a Rosetta-translated process reports:
        monkeypatch.setattr(platforms.os, "uname", lambda: os.uname_result(
            ("Darwin", "h", "25.5.0", "v", "x86_64")))

        assert platform_key("darwin") == "macos-arm64"

    def test_an_intel_bundle_selects_the_intel_feed(self, tmp_path, monkeypatch):
        exe = self._bundle(tmp_path, "x86_64")
        monkeypatch.setattr(platforms.sys, "executable", str(exe))
        monkeypatch.setattr(platforms.os, "uname", lambda: os.uname_result(
            ("Darwin", "h", "25.5.0", "v", "arm64")))

        assert platform_key("darwin") == "macos-x64"

    def test_outside_a_bundle_the_running_machine_is_still_the_answer(self, tmp_path, monkeypatch):
        # a source checkout has no BUILD_MANIFEST beside the interpreter
        loose = tmp_path / "venv" / "bin" / "python3"
        loose.parent.mkdir(parents=True)
        loose.write_text("", encoding="utf-8")
        monkeypatch.setattr(platforms.sys, "executable", str(loose))
        monkeypatch.setattr(platforms.os, "uname", lambda: os.uname_result(
            ("Darwin", "h", "25.5.0", "v", "arm64")))

        assert platform_key("darwin") == "macos-arm64"

    def test_an_explicit_machine_argument_still_wins(self, tmp_path, monkeypatch):
        exe = self._bundle(tmp_path, "arm64")
        monkeypatch.setattr(platforms.sys, "executable", str(exe))
        assert platform_key("darwin", "x86_64") == "macos-x64"

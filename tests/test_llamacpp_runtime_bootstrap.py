from __future__ import annotations

import hashlib
import json
import zipfile
from unittest import mock

import pytest

from core.llamacpp_capability_probe import LlamaCppBackendCandidate
from installer.llamacpp_runtime_bootstrap import (
    BootstrapError,
    InstalledBackend,
    _safe_extract,
    _verify_digest,
    fetch_release_assets,
    install_llamacpp_backend,
)


def _fake_urlopen_response(payload: bytes):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    return _Resp()


def test_fetch_release_assets_parses_github_api_payload() -> None:
    payload = json.dumps(
        {
            "assets": [
                {"name": "llama-b9856-bin-win-vulkan-x64.zip", "browser_download_url": "https://example/x.zip", "digest": "sha256:abc"},
                {"name": "README.md", "browser_download_url": "https://example/readme", "digest": ""},
            ]
        }
    ).encode("utf-8")

    with mock.patch("urllib.request.urlopen", return_value=_fake_urlopen_response(payload)):
        assets = fetch_release_assets(tag="b9856")

    assert assets["llama-b9856-bin-win-vulkan-x64.zip"]["url"] == "https://example/x.zip"
    assert assets["llama-b9856-bin-win-vulkan-x64.zip"]["digest"] == "sha256:abc"


def test_fetch_release_assets_raises_bootstrap_error_on_network_failure() -> None:
    with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
        with pytest.raises(BootstrapError):
            fetch_release_assets(tag="b9856")


def test_verify_digest_passes_for_matching_sha256(tmp_path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    digest = "sha256:" + hashlib.sha256(b"hello world").hexdigest()

    _verify_digest(path, expected_digest=digest)  # should not raise


def test_verify_digest_raises_and_deletes_on_mismatch(tmp_path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")

    with pytest.raises(BootstrapError):
        _verify_digest(path, expected_digest="sha256:" + "0" * 64)

    assert not path.exists()


def test_verify_digest_skips_when_no_digest_published(tmp_path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")

    _verify_digest(path, expected_digest="")  # should not raise, file kept
    assert path.exists()


def test_safe_extract_rejects_zip_slip(tmp_path) -> None:
    zip_path = tmp_path / "evil.zip"
    destination = tmp_path / "dest"
    destination.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../../escape.txt", "pwned")

    with pytest.raises(BootstrapError):
        _safe_extract(zip_path, destination=destination)


def test_safe_extract_extracts_normal_zip(tmp_path) -> None:
    zip_path = tmp_path / "normal.zip"
    destination = tmp_path / "dest"
    destination.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("llama-server.exe", "fake binary")

    _safe_extract(zip_path, destination=destination)

    assert (destination / "llama-server.exe").read_text() == "fake binary"


def test_install_llamacpp_backend_reuses_existing_when_manifest_matches(tmp_path) -> None:
    runtime_home = tmp_path
    install_dir = runtime_home / "runtime" / "llamacpp" / "vulkan"
    install_dir.mkdir(parents=True)
    server_exe = install_dir / "llama-server.exe"
    server_exe.write_text("fake")

    manifest_path = runtime_home / "config" / "llamacpp-runtime.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "vulkan": InstalledBackend(
                    backend="vulkan",
                    release_tag="b9856",
                    install_dir=str(install_dir),
                    server_exe_path=str(server_exe),
                ).to_dict()
            }
        ),
        encoding="utf-8",
    )

    candidate = LlamaCppBackendCandidate(
        backend="vulkan", vendor="nvidia",
        asset_name="llama-{tag}-bin-win-vulkan-x64.zip", requires_runtime_asset="", priority=1,
    )

    with mock.patch("installer.llamacpp_runtime_bootstrap.fetch_release_assets") as fetch_mock:
        installed = install_llamacpp_backend(candidate=candidate, runtime_home=runtime_home, tag="b9856")

    fetch_mock.assert_not_called()  # idempotent: no network call needed, manifest already satisfied
    assert installed.server_exe_path == str(server_exe)


# --- download hardening: authoritative timeout wall + sha256 pinning ------------------

import subprocess as _subprocess
import types as _types

import pytest as _pytest

from installer.llamacpp_runtime_bootstrap import _download_with_retry


def _partial(dest):
    return dest.with_suffix(dest.suffix + ".partial")


def test_download_timeout_is_bounded_and_fails_closed(monkeypatch, tmp_path) -> None:
    # A curl that ignores its own timers is killed by the Python subprocess timeout, and
    # the download fails closed (BootstrapError) rather than hanging the installer.
    monkeypatch.setattr("installer.llamacpp_runtime_bootstrap.shutil.which", lambda _x: "curl")

    def _hang(*_a, **kwargs):
        raise _subprocess.TimeoutExpired(cmd="curl", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr("installer.llamacpp_runtime_bootstrap.subprocess.run", _hang)
    dest = tmp_path / "asset.bin"
    with _pytest.raises(BootstrapError, match=r"exceeded|aborted"):
        _download_with_retry(url="https://example/x", destination=dest, max_seconds=5.0)
    assert not _partial(dest).exists()


def test_download_sha256_mismatch_rejects_and_deletes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("installer.llamacpp_runtime_bootstrap.shutil.which", lambda _x: "curl")
    dest = tmp_path / "asset.bin"

    def _write_bad(_cmd, **_k):
        _partial(dest).write_bytes(b"tampered payload")
        return _types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("installer.llamacpp_runtime_bootstrap.subprocess.run", _write_bad)
    with _pytest.raises(BootstrapError, match="sha256"):
        _download_with_retry(url="https://example/x", destination=dest, expected_sha256="00" * 32)
    assert not dest.exists()  # never promoted an unverified file


def test_download_sha256_match_succeeds(monkeypatch, tmp_path) -> None:
    import hashlib

    monkeypatch.setattr("installer.llamacpp_runtime_bootstrap.shutil.which", lambda _x: "curl")
    dest = tmp_path / "asset.bin"
    payload = b"legit llama.cpp asset"
    digest = hashlib.sha256(payload).hexdigest()

    def _write_good(_cmd, **_k):
        _partial(dest).write_bytes(payload)
        return _types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("installer.llamacpp_runtime_bootstrap.subprocess.run", _write_good)
    _download_with_retry(url="https://example/x", destination=dest, expected_sha256=digest)
    assert dest.read_bytes() == payload

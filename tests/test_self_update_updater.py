"""Tests for the detached self-updater: verify → swap → rollback, and data preservation."""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest import mock

import pytest

from installer.self_update import (
    backup_and_swap,
    compute_preserve_names,
    looks_like_vool_release,
    parse_sha256_sidecar,
    rollback_swap,
    run_update,
    safe_extract_zip,
    sha256_of_file,
    staged_top_level,
    verify_release_signature,
    verify_sha256,
)


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    return priv, pub_hex


def _sign_digest(priv, digest_hex: str) -> str:
    import base64

    return base64.b64encode(priv.sign(digest_hex.strip().lower().encode("utf-8"))).decode("ascii")

# --- pure helpers ----------------------------------------------------------------

def test_parse_sha256_sidecar_formats() -> None:
    h = "a" * 64
    assert parse_sha256_sidecar(h) == h
    assert parse_sha256_sidecar(f"{h}  vool-windows.zip") == h
    assert parse_sha256_sidecar(f"vool-windows.zip: {h.upper()}") == h
    assert parse_sha256_sidecar("not a hash") == ""
    assert parse_sha256_sidecar("") == ""


def test_verify_sha256(tmp_path) -> None:
    f = tmp_path / "blob"
    f.write_bytes(b"hello vool")
    digest = sha256_of_file(f)
    assert verify_sha256(f, digest) is True
    assert verify_sha256(f, "b" * 64) is False
    assert verify_sha256(f, "tooshort") is False


def _make_release_tree(root: Path, *, version: str = "0.5.0", extra: dict[str, str] | None = None) -> None:
    (root / "apps").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "vool_api_server.py").write_text(f"# server {version}\n", encoding="utf-8")
    (root / "core").mkdir(exist_ok=True)
    (root / "core" / "newmod.py").write_text("X = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    for rel, content in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _zip_release(src_top: Path, zip_path: Path) -> None:
    """Zip so members are prefixed by the top dir name (like a GitHub source zip)."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in src_top.rglob("*"):
            zf.write(p, p.relative_to(src_top.parent))


def test_safe_extract_single_top_dir(tmp_path) -> None:
    rel = tmp_path / "vool-0.5.0"
    _make_release_tree(rel)
    zpath = tmp_path / "pkg.zip"
    _zip_release(rel, zpath)
    root = safe_extract_zip(zpath, tmp_path / "staged")
    assert root.name == "vool-0.5.0"
    assert looks_like_vool_release(root)


def test_safe_extract_rejects_zip_slip(tmp_path) -> None:
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    with pytest.raises(ValueError, match="zip-slip"):
        safe_extract_zip(zpath, tmp_path / "staged")


def test_looks_like_vool_release_false_for_random_tree(tmp_path) -> None:
    (tmp_path / "random.txt").write_text("x", encoding="utf-8")
    assert looks_like_vool_release(tmp_path) is False


def test_compute_preserve_names_includes_data_and_nested_home(tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    # nested home inside the code dir must be preserved by its top-level name
    home = project / ".vool_runtime"
    names = compute_preserve_names(project, home)
    assert ".venv" in names and ".git" in names and "install_receipt.json" in names
    assert ".vool_runtime" in names
    # external home doesn't add a project-relative name but always-preserve still holds
    ext = compute_preserve_names(project, tmp_path / "home")
    assert ".venv" in ext


def test_backup_swap_and_rollback_preserve_data(tmp_path) -> None:
    project = tmp_path / "proj"
    (project / "apps").mkdir(parents=True)
    (project / "apps" / "vool_api_server.py").write_text("# OLD 0.4.0\n", encoding="utf-8")
    (project / "core").mkdir()
    (project / "core" / "oldmod.py").write_text("OLD = 1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text('version = "0.4.0"\n', encoding="utf-8")
    # protected user data + venv + receipt
    (project / ".venv").mkdir()
    (project / ".venv" / "python.exe").write_text("binary", encoding="utf-8")
    (project / "install_receipt.json").write_text('{"runtime_home": "X"}', encoding="utf-8")

    staged = tmp_path / "staged" / "vool-0.5.0"
    _make_release_tree(staged, version="0.5.0")
    preserve = compute_preserve_names(project, None)
    backup_dir = tmp_path / "backup"

    backup_and_swap(staged, project, preserve, backup_dir)
    # new code is in place
    assert "0.5.0" in (project / "apps" / "vool_api_server.py").read_text()
    assert (project / "core" / "newmod.py").exists()
    # a module removed in the new release is gone (dir-level replacement)
    assert not (project / "core" / "oldmod.py").exists()
    # protected data untouched
    assert (project / ".venv" / "python.exe").read_text() == "binary"
    assert (project / "install_receipt.json").exists()

    rollback_swap(project, backup_dir)
    # old code restored, protected data still intact
    assert "OLD 0.4.0" in (project / "apps" / "vool_api_server.py").read_text()
    assert (project / "core" / "oldmod.py").exists()
    assert not (project / "core" / "newmod.py").exists()
    assert (project / ".venv" / "python.exe").read_text() == "binary"


# --- orchestration (fakes) -------------------------------------------------------

def _prepare(tmp_path):
    """A project dir (old code + venv + receipt) and an external home with a wallet."""
    project = tmp_path / "proj"
    (project / "apps").mkdir(parents=True)
    (project / "apps" / "vool_api_server.py").write_text("# OLD 0.4.0\n", encoding="utf-8")
    (project / "core").mkdir()
    (project / "core" / "oldmod.py").write_text("OLD = 1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text('version = "0.4.0"\n', encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "python.exe").write_text("binary", encoding="utf-8")

    home = tmp_path / "home"
    (home / "data" / "keys").mkdir(parents=True)
    wallet = home / "data" / "keys" / "solana_wallet.enc"
    wallet.write_text("ENCRYPTED-WALLET-SEED", encoding="utf-8")
    (home / "data" / "vool_web0_v2.db").write_text("SQLITE", encoding="utf-8")

    # build a valid release zip
    rel = tmp_path / "src" / "vool-0.5.0"
    _make_release_tree(rel, version="0.5.0")
    zpath = tmp_path / "release.zip"
    _zip_release(rel, zpath)
    return project, home, wallet, zpath


def _downloader(zpath):
    import shutil

    return lambda url, dest: shutil.copyfile(zpath, dest)


def test_run_update_happy_path_preserves_wallet_and_swaps(tmp_path) -> None:
    project, home, wallet, zpath = _prepare(tmp_path)
    start = mock.Mock()
    stop = mock.Mock()
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/vool-windows.zip",
        sha256_url="https://example/vool-windows.zip.sha256",
        project_root=project,
        vool_home=home,
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: sha256_of_file(zpath),
        stop_server=stop,
        start_server=start,
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is True and result.stage == "done"
    # new code swapped in, old-only module removed
    assert "0.5.0" in (project / "apps" / "vool_api_server.py").read_text()
    assert (project / "core" / "newmod.py").exists()
    assert not (project / "core" / "oldmod.py").exists()
    # THE guarantee: wallet + DB + venv untouched
    assert wallet.read_text() == "ENCRYPTED-WALLET-SEED"
    assert (home / "data" / "vool_web0_v2.db").read_text() == "SQLITE"
    assert (project / ".venv" / "python.exe").read_text() == "binary"
    stop.assert_called()
    start.assert_called()


def test_run_update_checksum_mismatch_never_swaps(tmp_path) -> None:
    project, home, _wallet, zpath = _prepare(tmp_path)
    stop = mock.Mock()
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        project_root=project,
        vool_home=home,
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: "d" * 64,  # WRONG hash
        stop_server=stop,
        start_server=mock.Mock(),
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is False and result.stage == "verify"
    # nothing was stopped or swapped — old code intact
    stop.assert_not_called()
    assert "OLD 0.4.0" in (project / "apps" / "vool_api_server.py").read_text()
    assert not (project / "core" / "newmod.py").exists()


def test_run_update_unhealthy_rolls_back(tmp_path) -> None:
    project, home, wallet, zpath = _prepare(tmp_path)
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        project_root=project,
        vool_home=home,
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: sha256_of_file(zpath),
        stop_server=mock.Mock(),
        start_server=mock.Mock(),
        health_check=lambda: False,  # never comes up
        now_fn=lambda: 1000.0,
        health_timeout=0.0,  # skip the wait
    )
    assert result.ok is False and result.rolled_back is True and result.stage == "rolled_back"
    # old code restored, wallet safe
    assert "OLD 0.4.0" in (project / "apps" / "vool_api_server.py").read_text()
    assert (project / "core" / "oldmod.py").exists()
    assert wallet.read_text() == "ENCRYPTED-WALLET-SEED"


def test_run_update_rejects_non_release_zip(tmp_path) -> None:
    project, home, _wallet, _z = _prepare(tmp_path)
    bogus = tmp_path / "bogus.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("readme.txt", "not vool")
    result = run_update(
        target_version="v9.9.9",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        project_root=project,
        vool_home=home,
        downloader=_downloader(bogus),
        sha256_fetcher=lambda url: sha256_of_file(bogus),
        stop_server=mock.Mock(),
        start_server=mock.Mock(),
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is False and result.stage == "extract"
    assert "OLD 0.4.0" in (project / "apps" / "vool_api_server.py").read_text()


def test_staged_top_level_excludes_preserved(tmp_path) -> None:
    rel = tmp_path / "rel"
    _make_release_tree(rel)
    (rel / ".venv").mkdir()  # even if a release wrongly bundled .venv, it's excluded
    names = {p.name for p in staged_top_level(rel, compute_preserve_names(tmp_path, None))}
    assert "apps" in names and "core" in names and "pyproject.toml" in names
    assert ".venv" not in names
    assert "data" in compute_preserve_names(tmp_path, None)  # default data dir is preserved


def test_spawn_routes_through_intermediate_launcher(tmp_path) -> None:
    """CRITICAL: the updater must be launched via start_windows_detached (a separate
    process) so the server's taskkill /T can't kill the updater mid-swap."""
    from installer.self_update import spawn_detached_update

    with mock.patch("subprocess.Popen") as popen:
        ok = spawn_detached_update(
            target_version="v0.5.0",
            asset_url="https://x/vool-windows.zip",
            sha256_url="https://x/vool-windows.zip.sha256",
            project_root=tmp_path,
            vool_home=tmp_path / "home",
        )
    assert ok is True
    popen.assert_called_once()
    cmd = popen.call_args.args[0]
    # The launched process is the intermediate detacher, not the updater directly.
    assert "installer.start_windows_detached" in cmd
    # ...and the real updater command follows the "--" separator.
    sep = cmd.index("--")
    assert "installer.self_update" in cmd[sep:]
    assert "--target-version" in cmd[sep:]


def test_spawn_refuses_without_checksum(tmp_path) -> None:
    from installer.self_update import spawn_detached_update

    with mock.patch("subprocess.Popen") as popen:
        ok = spawn_detached_update(
            target_version="v0.5.0",
            asset_url="https://x/pkg.zip",
            sha256_url="",  # no checksum -> never launch
            project_root=tmp_path,
            vool_home=None,
        )
    assert ok is False
    popen.assert_not_called()


def test_stop_server_skips_non_python_pid(tmp_path) -> None:
    """A stale pidfile whose pid was reused by an unrelated process must not be killed."""
    from installer.self_update import _default_stop_server

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "vool_api.pid").write_text("4242", encoding="utf-8")
    with mock.patch("installer.self_update._pid_is_python", return_value=False), mock.patch(
        "subprocess.run"
    ) as run:
        _default_stop_server(home, tmp_path)
    run.assert_not_called()  # never issued taskkill on a non-python pid


def test_pid_is_python_uses_ps_on_macos(monkeypatch) -> None:
    """macOS has no /proc; the pid check must use `ps -p PID -o comm=` instead."""
    import subprocess
    import sys
    import types

    from installer import self_update

    monkeypatch.setattr(sys, "platform", "darwin")
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, capture_output=False, text=False, check=False):
        seen["cmd"] = list(cmd)
        return types.SimpleNamespace(stdout="python3.12\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert self_update._pid_is_python(4321) is True
    assert seen["cmd"][0] == "ps" and "comm=" in " ".join(seen["cmd"])

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="nginx\n"))
    assert self_update._pid_is_python(4321) is False


def test_default_start_server_uses_posix_launcher_not_bat(tmp_path, monkeypatch) -> None:
    """On POSIX the restart must run Start_VOOL.sh via bash, never bash the Windows .bat."""
    import subprocess
    import sys

    from installer import self_update

    (tmp_path / "Start_VOOL.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path / "Start_VOOL.bat").write_text("@echo off\n", encoding="utf-8")  # must NOT be chosen
    monkeypatch.setattr(sys, "platform", "linux")
    launched: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: launched.setdefault("cmd", list(cmd)))
    self_update._default_start_server(tmp_path)
    assert launched["cmd"][0] == "bash" and launched["cmd"][1].endswith("Start_VOOL.sh")


def test_default_start_server_prefers_command_on_macos(tmp_path, monkeypatch) -> None:
    import subprocess
    import sys

    from installer import self_update

    (tmp_path / "Start_VOOL.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path / "Start_VOOL.command").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "darwin")
    launched: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: launched.setdefault("cmd", list(cmd)))
    self_update._default_start_server(tmp_path)
    assert launched["cmd"][1].endswith("Start_VOOL.command")


# --- release-signature authenticity (Ed25519 on top of SHA-256) ------------------

def test_verify_release_signature_roundtrip() -> None:
    priv, pub = _keypair()
    digest = "a" * 64
    sig = _sign_digest(priv, digest)
    assert verify_release_signature(package_sha256_hex=digest, signature_b64=sig, publisher_pubkey_hex=pub) is True
    # A different digest, a different key, or garbage must not verify.
    assert verify_release_signature(package_sha256_hex="b" * 64, signature_b64=sig, publisher_pubkey_hex=pub) is False
    _, other = _keypair()
    assert verify_release_signature(package_sha256_hex=digest, signature_b64=sig, publisher_pubkey_hex=other) is False
    assert verify_release_signature(package_sha256_hex=digest, signature_b64="!!!", publisher_pubkey_hex=pub) is False
    assert verify_release_signature(package_sha256_hex=digest, signature_b64=sig, publisher_pubkey_hex="") is False


def test_run_update_accepts_valid_signature_when_publisher_key_set(tmp_path) -> None:
    project, home, _wallet, zpath = _prepare(tmp_path)
    priv, pub = _keypair()
    digest = sha256_of_file(zpath)
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        sig_url="https://example/pkg.zip.sig",
        publisher_pubkey=pub,
        project_root=project,
        vool_home=home,
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: digest,
        sig_fetcher=lambda url: _sign_digest(priv, digest),
        stop_server=mock.Mock(),
        start_server=mock.Mock(),
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is True and result.stage == "done"


def test_run_update_rejects_signature_from_wrong_key(tmp_path) -> None:
    project, _home, _wallet, zpath = _prepare(tmp_path)
    _priv, pub = _keypair()
    wrong_priv, _ = _keypair()
    digest = sha256_of_file(zpath)
    stop = mock.Mock()
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        sig_url="https://example/pkg.zip.sig",
        publisher_pubkey=pub,
        project_root=project,
        vool_home=tmp_path / "home",
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: digest,
        sig_fetcher=lambda url: _sign_digest(wrong_priv, digest),  # signed by the wrong key
        stop_server=stop,
        start_server=mock.Mock(),
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is False and result.stage == "verify"
    stop.assert_not_called()
    assert "OLD 0.4.0" in (project / "apps" / "vool_api_server.py").read_text()


def test_run_update_requires_signature_asset_when_publisher_key_set(tmp_path) -> None:
    project, _home, _wallet, zpath = _prepare(tmp_path)
    _priv, pub = _keypair()
    digest = sha256_of_file(zpath)
    stop = mock.Mock()
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        sig_url=None,  # no signature asset while a key is pinned -> refuse
        publisher_pubkey=pub,
        project_root=project,
        vool_home=tmp_path / "home",
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: digest,
        stop_server=stop,
        start_server=mock.Mock(),
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is False and result.stage == "verify"
    stop.assert_not_called()


def test_run_update_no_publisher_key_is_sha256_only(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VOOL_UPDATE_PUBLISHER_PUBKEY", raising=False)
    project, home, _wallet, zpath = _prepare(tmp_path)
    digest = sha256_of_file(zpath)
    result = run_update(
        target_version="v0.5.0",
        asset_url="https://example/pkg.zip",
        sha256_url="https://example/pkg.zip.sha256",
        project_root=project,
        vool_home=home,
        downloader=_downloader(zpath),
        sha256_fetcher=lambda url: digest,
        stop_server=mock.Mock(),
        start_server=mock.Mock(),
        health_check=lambda: True,
        now_fn=lambda: 1000.0,
    )
    assert result.ok is True and result.stage == "done"

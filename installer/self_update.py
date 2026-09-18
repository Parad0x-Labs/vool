"""
installer/self_update.py
========================
The detached VOOL self-updater: download → verify → stop → backup → swap → restart
→ health-check → rollback-on-failure.

Two hard guarantees:
  1. NEVER runs unverified code. The downloaded package is checked against the release's
     SHA-256 sidecar before anything is extracted or swapped; a mismatch aborts.
  2. NEVER touches user data. The swap replaces only code entries and always preserves a
     protected set: VOOL_HOME (wallet, spend policy, DB), .venv, .git, install_receipt.json,
     workspace, and the update backups themselves — even if VOOL_HOME nests inside the code
     dir. If the new code fails to come up healthy, the previous code is restored.

The dangerous filesystem operations (verify / extract / backup / swap / rollback) are pure
functions over paths, unit-tested with temp dirs. The process orchestration (stop / start /
health) is injectable so the whole flow can be exercised offline with fakes; the real
defaults do the Windows process work.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import shutil
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("vool.self_update")

# Hex Ed25519 public key of the release publisher. Empty until a maintainer generates a
# signing keypair and pins the public half here (or via VOOL_UPDATE_PUBLISHER_PUBKEY). While
# empty, releases are verified by SHA-256 integrity only; once set, a valid signature over the
# package hash is REQUIRED and a compromised mirror cannot forge one. The PRIVATE key is never
# stored in this repo.
PUBLISHER_PUBLIC_KEY_HEX = ""

HEALTH_URL = "http://127.0.0.1:11435/healthz"
HEALTH_TIMEOUT_SECONDS = 120.0
HEALTH_POLL_SECONDS = 3.0

# Top-level names under the code dir that a swap must NEVER move or overwrite.
_ALWAYS_PRESERVE = {
    ".git",
    ".venv",
    "venv",
    "env",
    "install_receipt.json",
    ".vool_local",
    ".vool_runtime",
    "workspace",
    "update_backups",
    ".update_work",
    "__pycache__",
    ".env",
    ".vool_updating.lock",
    "data",  # defensive: the default (vool_home=None) data dir lives here
}

# Watchdog-visible pause flag: while this file exists at the code root, the watchdog
# (vool_background.cmd) must NOT restart the server, so a swap is never interrupted.
_UPDATING_LOCK_NAME = ".vool_updating.lock"


@dataclass
class UpdateResult:
    ok: bool
    stage: str  # "verify" | "extract" | "swap" | "restart" | "done" | "rolled_back"
    message: str
    target_version: str = ""
    rolled_back: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "message": self.message,
            "target_version": self.target_version,
            "rolled_back": self.rolled_back,
        }


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #

def parse_sha256_sidecar(text: str) -> str:
    """Extract the hex digest from a .sha256 file. Accepts 'HEX', 'HEX  name', 'name: HEX'."""
    for token in str(text or "").replace(":", " ").split():
        candidate = token.strip().lower()
        if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
            return candidate
    return ""


def sha256_of_file(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify_sha256(path: Path, expected_hex: str) -> bool:
    expected = str(expected_hex or "").strip().lower()
    if len(expected) != 64:
        return False
    import hmac as _hmac

    return _hmac.compare_digest(sha256_of_file(path), expected)


def resolve_publisher_pubkey(explicit: str | None = None) -> str:
    """The pinned publisher Ed25519 public key (hex): explicit arg, else the environment
    override, else the compiled-in constant. Empty means signing is not configured yet."""
    for candidate in (explicit, os.environ.get("VOOL_UPDATE_PUBLISHER_PUBKEY"), PUBLISHER_PUBLIC_KEY_HEX):
        value = str(candidate or "").strip().lower()
        if value:
            return value
    return ""


def verify_release_signature(*, package_sha256_hex: str, signature_b64: str, publisher_pubkey_hex: str) -> bool:
    """Verify an Ed25519 signature over the release package's SHA-256 hex digest against the
    pinned publisher public key. Authenticity on top of the SHA-256 integrity check: a
    compromised mirror can serve a package whose bytes match a checksum it also controls, but
    cannot forge this signature without the publisher's private key."""
    digest = str(package_sha256_hex or "").strip().lower()
    pubkey = str(publisher_pubkey_hex or "").strip().lower()
    if len(digest) != 64 or not pubkey:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey))
        public_key.verify(base64.b64decode(str(signature_b64 or "").strip()), digest.encode("utf-8"))
        return True
    except Exception:
        return False


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def compute_preserve_names(project_root: Path, vool_home: Path | None) -> set[str]:
    """Top-level names under project_root that the swap must never touch."""
    names = set(_ALWAYS_PRESERVE)
    if vool_home is not None and _is_within(vool_home, project_root):
        rel = vool_home.resolve().relative_to(project_root.resolve())
        if rel.parts:
            names.add(rel.parts[0])
    return names


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract a zip guarding against path traversal (zip-slip). Returns the release root.

    If the archive is a single top-level directory (as GitHub source zips are), that
    directory is treated as the release root; otherwise dest_dir is.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target = (dest_dir / member).resolve()
            # Proper path-boundary check (not a string prefix, which a sibling like
            # `dest-evil/..` could spoof): the target must be inside dest_resolved.
            try:
                target.relative_to(dest_resolved)
            except ValueError as exc:
                raise ValueError(f"unsafe path in archive (zip-slip): {member}") from exc
        zf.extractall(dest_dir)
    entries = [p for p in dest_dir.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest_dir


def looks_like_vool_release(release_root: Path) -> bool:
    """Sanity-check that an extracted tree is actually a VOOL code release."""
    return (
        (release_root / "apps" / "vool_api_server.py").exists()
        and (release_root / "core").is_dir()
        and (release_root / "pyproject.toml").exists()
    )


def staged_top_level(release_root: Path, preserve: set[str]) -> list[Path]:
    """Top-level entries in the staged release that should replace code in project_root."""
    return [p for p in release_root.iterdir() if p.name not in preserve]


def _copy_into(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def backup_and_swap(release_root: Path, project_root: Path, preserve: set[str], backup_dir: Path) -> list[str]:
    """Move each to-be-replaced top-level code entry into backup_dir, then copy the staged
    version in. Returns the list of replaced top-level names (for rollback). Preserve-set
    entries are never moved or overwritten.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    replaced: list[str] = []
    for staged in staged_top_level(release_root, preserve):
        name = staged.name
        current = project_root / name
        if current.exists():
            shutil.move(str(current), str(backup_dir / name))
        replaced.append(name)
        _copy_into(staged, project_root / name)
    (backup_dir / "_replaced.json").write_text(json.dumps(replaced, sort_keys=True), encoding="utf-8")
    return replaced


def rollback_swap(project_root: Path, backup_dir: Path) -> None:
    """Restore the pre-update code from backup_dir: remove any swapped-in entry and move the
    backed-up original back into place."""
    manifest = backup_dir / "_replaced.json"
    try:
        replaced = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    except Exception:
        replaced = [p.name for p in backup_dir.iterdir() if p.name != "_replaced.json"]
    for name in replaced:
        swapped_in = project_root / name
        original = backup_dir / name
        if swapped_in.exists():
            if swapped_in.is_dir():
                shutil.rmtree(swapped_in, ignore_errors=True)
            else:
                swapped_in.unlink(missing_ok=True)
        if original.exists():
            shutil.move(str(original), str(swapped_in))


# --------------------------------------------------------------------------- #
# Default process orchestration (Windows). Injectable for tests.
# --------------------------------------------------------------------------- #

def _default_download(url: str, dest: Path, *, timeout: float = 60.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "vool-self-update"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _fetch_text(url: str, *, timeout: float = 30.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "vool-self-update"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _default_health_check(url: str = HEALTH_URL, *, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _wait_for_health(health_check: Callable[[], bool], *, deadline: float, now_fn: Callable[[], float], poll: float) -> bool:
    while now_fn() < deadline:
        if health_check():
            return True
        time.sleep(poll)
    return health_check()


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def run_update(
    *,
    target_version: str,
    asset_url: str,
    sha256_url: str,
    project_root: Path,
    vool_home: Path | None,
    work_dir: Path | None = None,
    downloader: Callable[[str, Path], None] | None = None,
    sha256_fetcher: Callable[[str], str] | None = None,
    sig_url: str | None = None,
    sig_fetcher: Callable[[str], str] | None = None,
    publisher_pubkey: str | None = None,
    stop_server: Callable[[], None] | None = None,
    start_server: Callable[[], None] | None = None,
    health_check: Callable[[], bool] | None = None,
    now_fn: Callable[[], float] = time.time,
    health_timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> UpdateResult:
    """Perform the full update. Verification precedes any swap; a failed health check rolls back."""
    project_root = Path(project_root).resolve()
    vool_home = Path(vool_home).resolve() if vool_home else None
    data_dir = (vool_home / "data") if vool_home else (project_root / "data")
    work_dir = Path(work_dir).resolve() if work_dir else (data_dir / ".update_work")
    downloader = downloader or (lambda url, dest: _default_download(url, dest))
    sha256_fetcher = sha256_fetcher or _fetch_text
    sig_fetcher = sig_fetcher or _fetch_text
    health_check = health_check or _default_health_check
    publisher_pubkey = resolve_publisher_pubkey(publisher_pubkey)

    if not asset_url or not sha256_url:
        return UpdateResult(False, "verify", "release is missing a package or checksum asset", target_version)

    # 1) Download + VERIFY before touching anything. Never run unverified code.
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        pkg = work_dir / "package.zip"
        downloader(asset_url, pkg)
        expected = parse_sha256_sidecar(sha256_fetcher(sha256_url))
        if not verify_sha256(pkg, expected):
            return UpdateResult(False, "verify", "package failed SHA-256 verification; refusing to install", target_version)
        # Authenticity: when a publisher key is pinned, a valid Ed25519 signature over the
        # verified package hash is REQUIRED (SHA-256 alone only proves the mirror is internally
        # consistent). While no key is configured, this is skipped and integrity-only stands.
        if publisher_pubkey:
            if not sig_url:
                return UpdateResult(False, "verify", "publisher key is configured but the release has no signature asset; refusing to install", target_version)
            signature_b64 = sig_fetcher(sig_url)
            if not verify_release_signature(
                package_sha256_hex=expected,
                signature_b64=signature_b64,
                publisher_pubkey_hex=publisher_pubkey,
            ):
                return UpdateResult(False, "verify", "release signature did not verify against the publisher key; refusing to install", target_version)
    except Exception as exc:
        return UpdateResult(False, "verify", f"download/verify failed: {exc}", target_version)

    # 2) Extract + sanity-check the tree.
    try:
        release_root = safe_extract_zip(pkg, work_dir / "staged")
        if not looks_like_vool_release(release_root):
            return UpdateResult(False, "extract", "downloaded package is not a valid VOOL release", target_version)
    except Exception as exc:
        return UpdateResult(False, "extract", f"extract failed: {exc}", target_version)

    preserve = compute_preserve_names(project_root, vool_home)
    backup_dir = data_dir / "update_backups" / f"{target_version or 'update'}-{int(now_fn())}"
    # At the code root so the watchdog (which only knows the code dir) can honour it.
    updating_lock = project_root / _UPDATING_LOCK_NAME

    # 3) Pause the watchdog, stop the server, swap, restart.
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        updating_lock.write_text(str(target_version), encoding="utf-8")
    except Exception:
        pass
    if stop_server is not None:
        try:
            stop_server()
        except Exception as exc:
            logger.warning("stop_server failed (continuing): %s", exc)

    try:
        backup_and_swap(release_root, project_root, preserve, backup_dir)
    except Exception as exc:
        # Swap failed mid-flight — restore whatever moved, then bring the old code back up.
        try:
            rollback_swap(project_root, backup_dir)
        except Exception as rexc:
            logger.error("rollback after failed swap ALSO failed: %s", rexc)
        _release_and_restart(updating_lock, start_server)
        return UpdateResult(False, "swap", f"swap failed and was rolled back: {exc}", target_version, rolled_back=True)

    # 4) Restart + health check. Roll back if the new code doesn't come up.
    _release_and_restart(updating_lock, start_server)
    healthy = _wait_for_health(
        health_check, deadline=now_fn() + health_timeout, now_fn=now_fn, poll=HEALTH_POLL_SECONDS
    )
    if healthy:
        _write_result(data_dir, UpdateResult(True, "done", f"updated to {target_version}", target_version))
        shutil.rmtree(work_dir, ignore_errors=True)
        return UpdateResult(True, "done", f"updated to {target_version}", target_version)

    # Unhealthy → roll back to the previous code and restart it.
    with contextlib.suppress(Exception):
        updating_lock.write_text(str(target_version), encoding="utf-8")
    if stop_server is not None:
        with contextlib.suppress(Exception):
            stop_server()
    rollback_swap(project_root, backup_dir)
    _release_and_restart(updating_lock, start_server)
    result = UpdateResult(False, "rolled_back", "new version did not come up healthy; restored previous version", target_version, rolled_back=True)
    _write_result(data_dir, result)
    return result


def _release_and_restart(updating_lock: Path, start_server: Callable[[], None] | None) -> None:
    with contextlib.suppress(Exception):
        updating_lock.unlink(missing_ok=True)
    if start_server is not None:
        try:
            start_server()
        except Exception as exc:
            logger.warning("start_server failed: %s", exc)


def _write_result(data_dir: Path, result: UpdateResult) -> None:
    with contextlib.suppress(Exception):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "update_result.json").write_text(json.dumps(result.to_dict(), sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Real process orchestration (Windows). Only reached in production, not in tests.
# --------------------------------------------------------------------------- #

def _pidfile(vool_home: Path | None, project_root: Path) -> Path:
    data = (vool_home / "data") if vool_home else (project_root / "data")
    return data / "vool_api.pid"


def _default_stop_server(vool_home: Path | None, project_root: Path) -> None:
    """Terminate the running API server via its pidfile (falls back to no-op)."""
    import os
    import signal
    import subprocess
    import sys

    pf = _pidfile(vool_home, project_root)
    if not pf.exists():
        return
    try:
        pid = int(pf.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return
    if pid <= 0 or pid == os.getpid():
        return
    if not _pid_is_python(pid):
        # Stale pidfile whose pid was reused by an unrelated process — never kill it.
        logger.warning("stop_server: pid %s is not a python process; refusing to kill (stale pidfile)", pid)
        return
    try:
        if sys.platform == "win32":
            # /T kills the process tree, /F forces it.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        logger.warning("stop_server: could not terminate pid %s: %s", pid, exc)


def _pid_is_python(pid: int) -> bool:
    """Best-effort check that `pid` is a live python process (guards stale-pid reuse)."""
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.lower()
            return ("python.exe" in out) or ("pythonw.exe" in out)
        if sys.platform == "darwin":
            # macOS has no /proc; use ps (matches installer/vool_stop.py:_pid_is_python_posix).
            out = subprocess.run(
                ["ps", "-p", str(int(pid)), "-o", "comm="],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.lower()
            return "python" in out
        # Linux: read the process command line.
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
            return b"python" in fh.read().lower()
    except Exception:
        # If we cannot confirm it's python, do NOT kill (fail safe).
        return False


def _default_start_server(project_root: Path) -> None:
    """Relaunch VOOL detached via the platform launcher (the keep-alive watchdog is the fallback).

    Windows uses Start_VOOL.bat; macOS/Linux use the installer-generated Start_VOOL.sh (or the
    .command twin on macOS) -- never bash a .bat, which cannot be interpreted by the shell.
    """
    import subprocess
    import sys

    if sys.platform == "win32":
        bat = project_root / "Start_VOOL.bat"
        if not bat.exists():
            logger.warning("start_server: %s not found; relying on the watchdog to restart", bat)
            return
        try:
            flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
            subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(project_root), creationflags=flags, close_fds=True)
        except Exception as exc:
            logger.warning("start_server: launch failed: %s", exc)
        return

    # POSIX: prefer the double-clickable .command on macOS, else the .sh; never bash a .bat.
    names = ["Start_VOOL.command", "Start_VOOL.sh"] if sys.platform == "darwin" else ["Start_VOOL.sh", "Start_VOOL.command"]
    launcher = next((project_root / n for n in names if (project_root / n).exists()), None)
    if launcher is None:
        logger.warning(
            "start_server: no POSIX launcher (Start_VOOL.sh/.command) in %s; relying on the watchdog to restart",
            project_root,
        )
        return
    try:
        subprocess.Popen(["bash", str(launcher)], cwd=str(project_root), start_new_session=True)
    except Exception as exc:
        logger.warning("start_server: launch failed: %s", exc)


def spawn_detached_update(
    *,
    target_version: str,
    asset_url: str,
    sha256_url: str,
    project_root: Path,
    vool_home: Path | None,
) -> bool:
    """Launch the updater as a DETACHED process so it survives the server it restarts.

    Returns True if the process was launched. Refuses (returns False) if the release is
    missing its package/checksum assets — we never launch an updater that can't verify.
    """
    if not asset_url or not sha256_url:
        return False
    import subprocess
    import sys

    root = Path(project_root).resolve()
    data_dir = (Path(vool_home).resolve() / "data") if vool_home else (root / "data")
    updater_cmd = [
        sys.executable,
        "-m",
        "installer.self_update",
        "--target-version",
        str(target_version),
        "--asset-url",
        str(asset_url),
        "--sha256-url",
        str(sha256_url),
        "--project-root",
        str(root),
    ]
    if vool_home:
        updater_cmd += ["--vool-home", str(Path(vool_home).resolve())]

    # CRITICAL: the updater stops the API server with `taskkill /T` (tree kill). If the
    # updater were a direct child of that server, it would kill ITSELF mid-swap (no
    # rollback, stuck lock, brick). So we launch it through start_windows_detached AS A
    # SEPARATE PROCESS: that intermediate breaks out of the server's job, spawns the
    # updater, and exits immediately — orphaning the updater so the server's tree-kill
    # can never reach it. (start_windows_detached is the project's proven detach primitive.)
    with contextlib.suppress(Exception):
        data_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = str(data_dir / "self_update.out.log")
    stderr_log = str(data_dir / "self_update.err.log")
    launcher_cmd = [
        sys.executable,
        "-m",
        "installer.start_windows_detached",
        "--cwd",
        str(root),
        "--stdout",
        stdout_log,
        "--stderr",
        stderr_log,
        "--",
        *updater_cmd,
    ]
    try:
        # C15: an updater run is unattended by definition — mark the child so its
        # preflight records the provenance and never lets a scratch home initiate an
        # interactive credential operation.
        child_env = dict(os.environ)
        child_env.setdefault("VOOL_UNATTENDED", "1")
        if vool_home:
            child_env["VOOL_HOME"] = str(Path(vool_home).resolve())
        if sys.platform == "win32":
            # DETACHED | NEW_GROUP | NO_WINDOW | BREAKAWAY_FROM_JOB
            flags = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000
            subprocess.Popen(launcher_cmd, cwd=str(root), creationflags=flags, close_fds=True, env=child_env)
        else:
            subprocess.Popen(launcher_cmd, cwd=str(root), start_new_session=True, env=child_env)
        return True
    except Exception as exc:
        logger.error("failed to launch detached updater: %s", exc)
        return False


def main(argv: list[str] | None = None) -> int:
    """Detached-process entry point: `python -m installer.self_update --target-version ...`."""
    import argparse

    parser = argparse.ArgumentParser(prog="vool-self-update")
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--asset-url", required=True)
    parser.add_argument("--sha256-url", required=True)
    parser.add_argument("--sig-url", default="")  # Ed25519 signature asset (used only once a publisher key is pinned)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--vool-home", default="")
    # C15: the ONE bounded unattended preflight — an updater run is unattended by
    # definition; a synthetic home gets non-interactive storage pinned.
    from core.unattended_preflight import preflight

    preflight('installer.self_update')
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    vool_home = Path(args.vool_home).resolve() if args.vool_home else None
    result = run_update(
        target_version=args.target_version,
        asset_url=args.asset_url,
        sha256_url=args.sha256_url,
        sig_url=args.sig_url or None,
        project_root=project_root,
        vool_home=vool_home,
        stop_server=lambda: _default_stop_server(vool_home, project_root),
        start_server=lambda: _default_start_server(project_root),
    )
    logger.info("self-update finished: %s", result.to_dict())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "UpdateResult",
    "backup_and_swap",
    "compute_preserve_names",
    "looks_like_vool_release",
    "main",
    "parse_sha256_sidecar",
    "rollback_swap",
    "run_update",
    "safe_extract_zip",
    "sha256_of_file",
    "spawn_detached_update",
    "staged_top_level",
    "verify_sha256",
]

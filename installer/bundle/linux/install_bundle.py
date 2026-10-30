"""Own only the per-user Linux package; runtime profiles remain outside it."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

MARKER = "vool-linux-bundle-v1\n"
DESKTOP_MARKER = "X-VOOL-Linux-Bundle=true"
SERVER_ARG = "-m apps.vool_api_server"
PIDFILE_RELATIVE = Path("data/vool_api.pid")
STOP_POLL_SECONDS = 0.2
TERM_WAIT_SECONDS = 10.0
KILL_WAIT_SECONDS = 10.0


def safe_directory(path: Path) -> None:
    for parent in reversed([path, *path.parents]):
        if parent.is_symlink():
            raise ValueError(f"Refusing symlink directory: {parent}")
    path.mkdir(parents=True, exist_ok=True)


def layout(home: Path) -> tuple[Path, Path, Path]:
    if not home.is_absolute() or not home.is_dir() or any(character in str(home) for character in "\n\r\t"):
        raise ValueError("HOME must name an existing absolute directory")
    return home / ".local/share/vool", home / ".local/bin/vool", home / ".local/share/applications/vool.desktop"


def owned_root(root: Path) -> bool:
    marker = root / ".vool-linux-bundle"
    return not root.is_symlink() and not marker.is_symlink() and marker.is_file() and marker.read_text() == MARKER


def check_entrypoints(root: Path, binary: Path, desktop: Path) -> None:
    if os.path.lexists(binary) and (not binary.is_symlink() or os.readlink(binary) != str(root / "current/vool")):
        raise ValueError("Refusing to replace an unrelated vool launcher")
    if os.path.lexists(desktop) and (
        desktop.is_symlink() or not desktop.is_file() or DESKTOP_MARKER not in desktop.read_text().splitlines()
    ):
        raise ValueError("Refusing to replace an unrelated desktop entry")


def verify_package(package: Path) -> str:
    sha = (package / "SOURCE_COMMIT").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid package source identity")
    required = {"vool", "launch.py", "install_bundle.py", "install.sh", "uninstall.sh", "python/bin/python3", "app/apps/vool_api_server.py"}
    seen = set()
    for line in (package / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        path = package / name
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not path.resolve().is_relative_to(package.resolve()):
            raise ValueError("Invalid package checksum path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Package checksum mismatch: {name}")
        seen.add(name.removeprefix("./"))
    # python3 is normally a relative symlink, so its target is hashed by the build.
    for name in required:
        path = package / name
        if not path.is_file() or not path.resolve().is_relative_to(package.resolve()):
            raise ValueError(f"Incomplete package: {name}")
        if not path.is_symlink() and name not in seen:
            raise ValueError(f"Unverified package file: {name}")
    for path in package.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(package.resolve()):
            raise ValueError("Package contains an external symlink")
        if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS" and path.relative_to(package).as_posix() not in seen:
            raise ValueError("Package contains an unverified file")
    return sha


def desktop_text(root: Path) -> str:
    # Desktop Entry Exec has its own escaping, separate from shell quoting.
    executable = str(root / "current/vool").replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        executable = executable.replace(character, "\\" + character)
    executable = executable.replace("\\", "\\\\").replace("%", "%%")
    icon = str(root / "current/vool.png").replace("\\", "\\\\")
    return f"[Desktop Entry]\nType=Application\nName=VOOL\nExec=\"{executable}\"\nIcon={icon}\nTerminal=false\nCategories=Utility;\n{DESKTOP_MARKER}\n"


def install(package: Path, home: Path) -> None:
    sha = verify_package(package)
    root, binary, desktop = layout(home)
    new_root = not os.path.lexists(root)
    if not new_root and not owned_root(root):
        raise ValueError("Refusing an unowned install directory")
    check_entrypoints(root, binary, desktop)
    for folder in (root, binary.parent, desktop.parent):
        safe_directory(folder)
    versions = root / "versions"
    safe_directory(versions)
    lock = root / ".install-lock"
    lock.mkdir()  # Concurrent installs fail closed; never reclaim an unknown lock.
    stage = None
    created_version = False
    current = root / "current"
    previous = os.readlink(current) if current.is_symlink() else None
    previous_desktop = desktop.read_bytes() if desktop.exists() else None
    previous_binary = binary.is_symlink()
    version = versions / sha
    switched = False
    try:
        if os.path.lexists(current) and (previous is None or not current.resolve().is_relative_to(versions.resolve())):
            raise ValueError("Invalid current install link")
        if version.exists() or version.is_symlink():
            if version.is_symlink() or verify_package(version) != sha:
                raise ValueError("Invalid existing package version")
            if (version / "SHA256SUMS").read_bytes() != (package / "SHA256SUMS").read_bytes():
                raise ValueError("Source identity collision: package bytes differ")
        else:
            stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
            shutil.copytree(package, stage, dirs_exist_ok=True, symlinks=True)
            verify_package(stage)
            stage.rename(version)
            stage = None
            created_version = True
        # Prepare entrypoints before changing the active package. On failure,
        # restore the previous links/content and leave runtime data untouched.
        link = root / ".next-current"
        link.symlink_to(Path("versions") / sha)
        os.replace(link, current)
        switched = True
        if not previous_binary:
            binary.symlink_to(root / "current/vool")
        with tempfile.NamedTemporaryFile(dir=desktop.parent, delete=False) as temporary:
            temporary.write(desktop_text(root).encode())
            desktop_stage = Path(temporary.name)
        os.replace(desktop_stage, desktop)
        (root / ".vool-linux-bundle").write_text(MARKER)
    except Exception:
        if switched:
            current.unlink()
            if previous is not None:
                current.symlink_to(previous)
        if not previous_binary and binary.is_symlink():
            binary.unlink()
        if previous_desktop is not None:
            desktop.write_bytes(previous_desktop)
        elif desktop.exists():
            desktop.unlink()
        if created_version:
            shutil.rmtree(version)
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        lock.rmdir()
        if new_root and not owned_root(root):
            if versions.exists() and not any(versions.iterdir()):
                versions.rmdir()
            if not any(root.iterdir()):
                root.rmdir()
    print(f"Installed VOOL at {root}; launch {binary} (add its directory to PATH if needed).")
    print(f"Uninstall: bash {root}/current/uninstall.sh (runtime profiles are preserved).")


def runtime_home_for(home: Path) -> Path:
    """Where the installed package keeps its chats and settings (launch.py parity)."""
    env = os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME")
    if env:
        return Path(env)
    canonical = home / ".vool_runtime"
    legacy = home / ".nulla_runtime"
    if not canonical.exists() and legacy.exists():
        return legacy
    return canonical


def process_cwd(pid: int) -> Path | None:
    """The working directory of ``pid``, or None when it cannot be proven."""
    proc_cwd = Path(f"/proc/{int(pid)}/cwd")
    if proc_cwd.exists():
        try:
            return proc_cwd.resolve()
        except OSError:
            return None
    listing = subprocess.run(
        ["lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if listing.returncode != 0:
        return None
    for line in listing.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            return Path(line[1:])
    return None


def _pid_has_exited(pid: int) -> bool:
    """Gone from the kernel, or a zombie the parent has not reaped yet."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                             capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return False
    return out.returncode != 0 or out.stdout.strip().startswith("Z")


def owned_server_pid(home: Path, root: Path) -> int:
    """This package's own API server pid, proven the way the updater proves it.

    The pidfile alone proves nothing (numbers get reused): the command line must
    be VOOL's server and the working directory must sit inside this package's
    own ``versions`` tree. Anything else returns 0 and is never signalled.
    """
    pidfile = runtime_home_for(home) / PIDFILE_RELATIVE
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0
    if pid <= 1 or pid == os.getpid():
        return 0
    try:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                                 capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        # A live pidfile number that cannot be inspected must block removal,
        # never silently proceed under a server we failed to check.
        raise OSError(f"could not check whether VOOL is running: {exc}") from exc
    if command.returncode or SERVER_ARG not in command.stdout:
        return 0  # no such process (stale number) or not VOOL's server
    try:
        cwd = process_cwd(pid)
    except OSError as exc:
        raise OSError(f"could not prove the running VOOL belongs to this package: {exc}") from exc
    if cwd is None:
        raise OSError("could not prove the running VOOL belongs to this package")
    versions = (Path(root) / "versions").resolve()
    return pid if cwd.parent.parent == versions and cwd.name == "app" else 0


def _wait_until_exited(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if _pid_has_exited(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(STOP_POLL_SECONDS)


def stop_running_server(home: Path, root: Path, *, term_wait: float = TERM_WAIT_SECONDS,
                        kill_wait: float = KILL_WAIT_SECONDS) -> tuple[bool, str | None]:
    """Stop the package's own running server before its files are removed.

    A live server with its package deleted underneath it keeps the port and
    answers with errors, so removal waits for a real exit: SIGTERM, a bounded
    wait, SIGKILL, another bounded wait. Refuses (removing nothing) when the
    server will not die. A process the ownership check did not prove to be this
    package's server is never touched.
    """
    pid = owned_server_pid(home, root)
    if pid <= 0:
        return True, None
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass  # exited between the check and the signal
    if _wait_until_exited(pid, term_wait):
        return True, None
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    if _wait_until_exited(pid, kill_wait):
        return True, None
    return False, ("VOOL is still running and could not be stopped, so nothing was removed. "
                   "Close VOOL and run this uninstall again.")


def _report_kept_data(home: Path) -> None:
    runtime_home = runtime_home_for(home)
    if runtime_home.is_dir():
        print(f"Your chats and settings were kept, in: {runtime_home}")
        print("A fresh VOOL install will find them there again.")
        print("To delete them as well, run this uninstall again with --remove-data")
    else:
        print("No saved chats and settings were found; nothing was kept.")


def _remove_profile_data(home: Path, *, assume_yes: bool,
                         prompt: Callable[..., str] = input) -> None:
    runtime_home = runtime_home_for(home)
    if not runtime_home.exists() and not runtime_home.is_symlink():
        return
    resolved = runtime_home.resolve()
    home_resolved = Path(home).resolve()
    if runtime_home.is_symlink() or not runtime_home.is_dir():
        raise ValueError(f"Refusing to remove {runtime_home}: it is not a plain VOOL data folder")
    if resolved == home_resolved or resolved == resolved.parent:
        raise ValueError("Refusing to remove the home folder itself")
    if not assume_yes:
        answer = prompt(f"Delete your saved chats and settings in {runtime_home} too? [no]: ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Kept your chats and settings.")
            return
    shutil.rmtree(runtime_home)
    print("Deleted your saved chats and settings.")


def uninstall(home: Path, *, remove_data: bool = False, assume_yes: bool = False,
              prompt: Callable[..., str] = input) -> None:
    root, binary, desktop = layout(home)
    if not root.exists():
        print("VOOL Linux package is already absent.")
        _report_kept_data(home)
        if remove_data:
            _remove_profile_data(home, assume_yes=assume_yes, prompt=prompt)
        return
    stopped, refusal = stop_running_server(home, root)
    if not stopped:
        raise ValueError(refusal)
    if not owned_root(root):
        raise ValueError("Refusing to remove an unowned install directory")
    check_entrypoints(root, binary, desktop)
    for folder in (root, binary.parent, desktop.parent):
        safe_directory(folder)
    lock = root / ".install-lock"
    lock.mkdir()
    try:
        versions = root / "versions"
        if versions.is_symlink():
            raise ValueError("Refusing symlink versions directory")
        # All version folders are immutable owned packages, never profiles.
        if versions.exists():
            for version in versions.iterdir():
                if version.is_symlink() or not re.fullmatch(r"[0-9a-f]{40}", version.name):
                    raise ValueError("Refusing unknown content in package versions")
                verify_package(version)
            shutil.rmtree(versions)
        if binary.is_symlink():
            binary.unlink()
        if desktop.exists():
            desktop.unlink()
        current = root / "current"
        if current.is_symlink():
            current.unlink()
    finally:
        lock.rmdir()
    # Retain ownership when unrelated files keep the directory alive, so a
    # repeated uninstall or reinstall remains safe and idempotent.
    if list(root.iterdir()) == [root / ".vool-linux-bundle"]:
        (root / ".vool-linux-bundle").unlink()
        root.rmdir()
    print("Uninstalled VOOL Linux package; unrelated files were preserved.")
    _report_kept_data(home)
    if remove_data:
        _remove_profile_data(home, assume_yes=assume_yes, prompt=prompt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--package", type=Path)
    parser.add_argument("--remove-data", action="store_true",
                        help="uninstall: also delete your saved chats and settings (asks once)")
    parser.add_argument("--yes", action="store_true",
                        help="uninstall: skip the delete-data question asked by --remove-data")
    args = parser.parse_args()
    try:
        home = Path(os.environ.get("HOME", ""))
        if args.action == "install":
            if args.package is None:
                parser.error("install requires --package")
            install(args.package.resolve(), home)
        else:
            uninstall(home, remove_data=args.remove_data, assume_yes=args.yes)
    except (OSError, ValueError) as exc:
        prefix = "Installation refused" if args.action == "install" else "Uninstall refused"
        print(f"{prefix}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

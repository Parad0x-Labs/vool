"""Own only the per-user Linux package; runtime profiles remain outside it."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

MARKER = "vool-linux-bundle-v1\n"
DESKTOP_MARKER = "X-VOOL-Linux-Bundle=true"


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


def uninstall(home: Path) -> None:
    root, binary, desktop = layout(home)
    if not root.exists():
        print("VOOL Linux package is already absent.")
        return
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
    print("Uninstalled VOOL Linux package; user profiles and unrelated files preserved.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    try:
        home = Path(os.environ.get("HOME", ""))
        if args.action == "install":
            if args.package is None:
                parser.error("install requires --package")
            install(args.package.resolve(), home)
        else:
            uninstall(home)
    except (OSError, ValueError) as exc:
        print(f"Installation refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

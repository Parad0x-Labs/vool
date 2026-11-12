"""Build the VOOL .deb around the self-contained Linux package.

usage: python3 installer/bundle/build_linux_deb.py --package dist/vool-<version>-linux-x86_64.tar.gz [--out DIRECTORY]

The .deb adds no second way to run VOOL. It carries the exact package from the tarball, checked against the
package's own SHA256SUMS, in /opt/vool/package, plus /usr/bin/vool and a menu entry. The first time a person
opens VOOL, /usr/bin/vool sets up their own copy in ~/.local/share/vool with the package's own installer (the
same place a tarball install uses), and VOOL keeps that copy up to date itself. After an apt upgrade, the
newer system copy is set up for each person the next time they open VOOL (system_copy.py). Removing the .deb
leaves every person's copy, chats and settings where they are.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PACKAGE_DIR = Path("opt/vool/package")
# Every package needs these system libraries (checked by the tests against the package's own ELF files);
# libc6's lowest version is read from the package itself. The rest is the native window (python3-gi and
# GTK/WebKit under the system Python).
SYSTEM_LIBRARIES = ("libgcc-s1", "libstdc++6", "zlib1g")
WINDOW = ("python3 (>= 3.10)", "python3-gi", "gir1.2-gtk-3.0", "gir1.2-webkit2-4.1")
RECOMMENDS = ("libnotify-bin", "xdg-utils")
MAINTAINER = "Parad0x Labs <240776969+Parad0x-Labs@users.noreply.github.com>"
HOMEPAGE = "https://github.com/Parad0x-Labs/vool"
DESCRIPTION = """\
Local-first personal agent that remembers more and runs on your machine
 VOOL is an open-source personal agent that keeps your chats, projects and
 memory on this computer. Start on a local model or your own key.
 .
 Each person on this computer gets their own copy of VOOL the first time they
 open it, and VOOL keeps that copy up to date by itself. Removing this package
 leaves each person's copy, chats and settings in place.
"""

LAUNCHER = """\
#!/usr/bin/env bash
# VOOL from the .deb. Each person runs their own copy in ~/.local/share/vool, which VOOL keeps up to date by
# itself. It is set up from /opt/vool/package the first time this person opens VOOL, and again after an apt
# upgrade brings a newer one (system_copy.py decides, and never undoes an in-app update).
set -euo pipefail
SYSTEM_PACKAGE=/opt/vool/package
OWN_ROOT="${HOME:?}/.local/share/vool"
tell() {
  printf '%s\\n' "$1" >&2
  if [[ ! -t 2 ]] && command -v notify-send >/dev/null 2>&1; then
    notify-send --app-name=VOOL VOOL "$1" >/dev/null 2>&1 || true
  fi
}
if [[ ! -x "${OWN_ROOT}/current/vool" ]]; then
  if ! output="$(bash "${SYSTEM_PACKAGE}/install.sh" 2>&1)"; then
    tell "VOOL could not be set up in ${OWN_ROOT}: ${output##*$'\\n'}"
    exit 1
  fi
  export VOOL_SYSTEM_COPY_SET_UP=1
elif "${SYSTEM_PACKAGE}/python/bin/python3" -B -I "${SYSTEM_PACKAGE}/system_copy.py" \\
    "$(readlink -f -- "${OWN_ROOT}/current")" "$@"; then
  export VOOL_SYSTEM_COPY_SET_UP=1
fi
exec "${OWN_ROOT}/current/vool" "$@"
"""

DESKTOP = """\
[Desktop Entry]
Type=Application
Name=VOOL
Comment=Local-first personal agent
Exec=/usr/bin/vool
Icon=vool
Terminal=false
Categories=Utility;
StartupWMClass=VOOL
"""

POSTRM = """\
#!/bin/sh
set -e
case "$1" in
  remove|purge)
    echo "VOOL: each person's own copy in ~/.local/share/vool, and their chats and settings, are kept."
    echo "VOOL: to remove a person's copy too, that person runs: bash ~/.local/share/vool/current/uninstall.sh"
    ;;
esac
exit 0
"""

_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?")


def deb_version(version: str) -> str:
    """0.8.0-beta -> 0.8.0~beta, so dpkg orders a prerelease below its release, as the updater does."""
    match = _SEMVER.fullmatch(version)
    if match is None:
        raise ValueError(f"Refusing a version dpkg cannot order the way the updater does: {version!r}")
    major, minor, patch, pre = match.groups()
    return f"{major}.{minor}.{patch}" + (f"~{pre}" if pre else "")


def lowest_glibc(package: Path) -> str:
    """The highest GLIBC_x.y symbol version any ELF file in the package asks for."""
    highest = (2, 17)
    for path in package.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(4) != b"\x7fELF":
                continue
        for match in re.finditer(rb"GLIBC_(\d+)\.(\d+)(?![\d.])", path.read_bytes()):
            highest = max(highest, (int(match.group(1)), int(match.group(2))))
    return f"{highest[0]}.{highest[1]}"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"Not a PNG image: {path}")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def extract(tarball: Path, into: Path) -> Path:
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError(f"Refusing package member: {member.name}")
            if member.issym():
                target = Path(os.path.normpath(name.parent / member.linkname))
                if Path(member.linkname).is_absolute() or ".." in target.parts:
                    raise ValueError(f"Refusing package symlink: {member.name} -> {member.linkname}")
        # every member is checked above; the data filter (where this Python has it) checks again
        archive.extractall(into, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    tops = list(into.iterdir())
    if len(tops) != 1 or not tops[0].is_dir() or tops[0].is_symlink():
        raise ValueError("Expected one package folder in the tarball")
    return tops[0]


def verify(package: Path) -> str:
    """Check every file against SHA256SUMS with the package's own Python and its own installer code."""
    check = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import install_bundle; "
             "print(install_bundle.verify_package(Path(sys.argv[1])))")
    result = subprocess.run([str(package / "python/bin/python3"), "-B", "-I", "-c", check, str(package)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"The package failed its own verification: {result.stderr.strip()[-500:]}")
    return result.stdout.strip()


def normalize(root: Path, epoch: int) -> None:
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            mode = path.stat().st_mode
            os.chmod(path, 0o755 if path.is_dir() or mode & 0o111 else 0o644)
        os.utime(path, (epoch, epoch), follow_symlinks=False)


def installed_size_kib(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.relative_to(root).parts[0] == "DEBIAN":
            continue
        total += -(-path.lstat().st_size // 1024) if path.is_file() and not path.is_symlink() else 1
    return total


def md5sums(root: Path) -> str:
    lines = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] != "DEBIAN" and path.is_file() and not path.is_symlink():
            lines.append(f"{hashlib.md5(path.read_bytes()).hexdigest()}  {relative.as_posix()}\n")
    return "".join(lines)


def control(version: str, glibc: str, installed_kib: int) -> str:
    depends = ", ".join((f"libc6 (>= {glibc})", *SYSTEM_LIBRARIES, *WINDOW))
    return (f"Package: vool\nVersion: {deb_version(version)}\nArchitecture: amd64\nMaintainer: {MAINTAINER}\n"
            f"Installed-Size: {installed_kib}\nDepends: {depends}\nRecommends: {', '.join(RECOMMENDS)}\n"
            f"Section: utils\nPriority: optional\nHomepage: {HOMEPAGE}\nDescription: {DESCRIPTION}")


def build(tarball: Path, out: Path, epoch: int | None = None) -> Path:
    if shutil.which("dpkg-deb") is None:
        raise ValueError("dpkg-deb is required to build the .deb")
    out.mkdir(parents=True, exist_ok=True)
    name = tarball.name.removesuffix(".tar.gz")
    if not re.fullmatch(r"vool-.+-linux-x86_64", name):
        raise ValueError(f"Expected vool-<version>-linux-x86_64.tar.gz, not {tarball.name}")
    deb = out / f"{name}.deb"
    if deb.exists() or Path(f"{deb}.sha256").exists():
        raise ValueError("The .deb already exists; refusing to overwrite")
    with tempfile.TemporaryDirectory(prefix=".deb-build-", dir=out) as scratch:
        package = extract(tarball, Path(scratch) / "extracted")
        sha = verify(package)
        version = (package / "VERSION").read_text(encoding="utf-8").strip()
        if name != f"vool-{version}-linux-x86_64":
            raise ValueError(f"The tarball name does not match the package's VERSION {version}")
        if png_size(package / "vool.png") != (512, 512):
            raise ValueError("The package icon must be 512x512")
        root = Path(scratch) / "root"
        (root / PACKAGE_DIR).parent.mkdir(parents=True)
        package.rename(root / PACKAGE_DIR)
        package = root / PACKAGE_DIR
        files = {
            "usr/bin/vool": LAUNCHER,
            "usr/share/applications/vool.desktop": DESKTOP,
            "DEBIAN/postrm": POSTRM,
        }
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        for executable in ("usr/bin/vool", "DEBIAN/postrm"):
            os.chmod(root / executable, 0o755)
        icon = root / "usr/share/icons/hicolor/512x512/apps/vool.png"
        icon.parent.mkdir(parents=True)
        shutil.copyfile(package / "vool.png", icon)
        doc = root / "usr/share/doc/vool"
        doc.mkdir(parents=True)
        (doc / "copyright").write_text(
            f"VOOL {version} ({sha})\nSource: {HOMEPAGE}\n\n" + (package / "app/LICENSE").read_text(encoding="utf-8"),
            encoding="utf-8")
        if epoch is None:
            epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or (package / "SOURCE_COMMIT").stat().st_mtime)
        (root / "DEBIAN/md5sums").write_text(md5sums(root), encoding="utf-8")
        (root / "DEBIAN/control").write_text(control(version, lowest_glibc(package), installed_size_kib(root)),
                                             encoding="utf-8")
        normalize(root, epoch)
        staged = Path(scratch) / deb.name
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(staged)],
                       check=True, env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)), stdout=subprocess.DEVNULL)
        staged.rename(deb)
    digest = hashlib.sha256(deb.read_bytes()).hexdigest()
    Path(f"{deb}.sha256").write_text(f"{digest}  {deb.name}\n", encoding="utf-8")
    print(f"Built {deb.name} (vool {deb_version(version)}) from {sha}")
    return deb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the VOOL .deb around the self-contained Linux package")
    parser.add_argument("--package", type=Path, required=True, help="vool-<version>-linux-x86_64.tar.gz")
    parser.add_argument("--out", type=Path, default=None, help="folder for the .deb (default: the tarball's folder)")
    args = parser.parse_args(argv)
    tarball = args.package.resolve()
    try:
        build(tarball, (args.out or tarball.parent).resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"The .deb was not built: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

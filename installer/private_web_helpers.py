#!/usr/bin/env python3
"""Fetch, verify and place the two helper programs Private web runs.

* tor        -- the official Tor Expert Bundle (BSD-3), pinned by version and SHA-256. The
                SHA-256 values below are copied from the Tor Project's sha256sums-signed-build.txt
                for that release, whose signature was checked against the Tor Browser Developers
                signing key (EF6E 286D DA85 EA2A 4BA7 DE68 4E2C 6E87 9329 8290).
* wireproxy  -- user-space WireGuard client (ISC), built from a pinned module version with Go,
                or taken from a prebuilt file whose SHA-256 is pinned below.

Usage:
    python installer/private_web_helpers.py --platform macos-arm64 --dest <app root>
    python installer/private_web_helpers.py --platform linux-x64 --dest <app root> --cache <dir>

Files land where VOOL looks for them (core.tor_runtime / core.wireguard_runtime):
    <dest>/third_party/tor/<platform>/tor (+ its libraries, geoip data)
    <dest>/third_party/wireproxy/<platform>/wireproxy
Any mismatch stops with an error; nothing unverified is placed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

TOR_RELEASE = "16.0.1"  # Tor Browser release whose expert bundle carries tor 0.4.9.16
TOR_BASE = f"https://archive.torproject.org/tor-package-archive/torbrowser/{TOR_RELEASE}"
TOR_BUNDLES = {
    "macos-arm64": ("tor-expert-bundle-macos-aarch64-16.0.1.tar.gz", "0b7d022017c31e3f972d06465bcd7ac50feb66ac825547419a934914fe4bb156"),
    "macos-x64": ("tor-expert-bundle-macos-x86_64-16.0.1.tar.gz", "15464d99adc3bb4de1308260d79878336b04a1ac1aeff83e10f192de7d235946"),
    "linux-x64": ("tor-expert-bundle-linux-x86_64-16.0.1.tar.gz", "7039cb16060ab22e669852e55774d894b55f25ea8bd0ab7194d3478429a58725"),
}

WIREPROXY_MODULE = "github.com/windtf/wireproxy"
WIREPROXY_VERSION = "v1.1.3"
#: SHA-256 of `CGO_ENABLED=0 go build -trimpath -ldflags "-s -w"` of the pinned version with
#: go1.26.0, per platform. A prebuilt file passed with --wireproxy-file must match.
WIREPROXY_SHA256 = {
    "macos-arm64": "66f88d5ae7911931a4fa43fbd4798c99f40f9151469973edf6804c567eaa1549",
    "macos-x64": "fa4d63f7c6b131f39fdebbaede4ab12906888db844a90265ebdff76b02dc4979",
    "linux-x64": "bbbc92f6245133bda557a582ea9192b56908ed33fd15fc38993dc21e6ce84c43",
}
_GO_TARGET = {"macos-arm64": ("darwin", "arm64"), "macos-x64": ("darwin", "amd64"), "linux-x64": ("linux", "amd64")}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_tor(platform: str, cache: Path) -> Path:
    name, expected = TOR_BUNDLES[platform]
    target = cache / name
    if not target.exists() or sha256_file(target) != expected:
        cache.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        with urllib.request.urlopen(f"{TOR_BASE}/{name}", timeout=120) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out)
        os.replace(tmp, target)
    actual = sha256_file(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        raise SystemExit(f"tor bundle {name}: SHA-256 {actual} does not match the pinned {expected}")
    return target


def place_tor(platform: str, archive: Path, dest: Path) -> Path:
    out = dest / "third_party" / "tor" / platform
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if not parts or ".." in parts or member.name.startswith("/") or member.issym() or member.islnk():
                continue
            # tor/ holds the program and its libraries; data/ the geoip files; docs/ the licences.
            if parts[0] == "tor" and "pluggable_transports" not in parts and member.isfile():
                member.name = str(Path(*parts[1:]))
            elif parts[0] in {"data", "docs"} and member.isfile():
                member.name = str(Path(*parts))
            else:
                continue
            tar.extract(member, out, filter="data")
            extracted = out / member.name
            extracted.chmod(0o755 if parts[0] == "tor" else 0o644)
    binary = out / ("tor.exe" if platform.startswith("windows") else "tor")
    if not binary.is_file():
        raise SystemExit(f"tor bundle {archive.name} has no tor program")
    binary.chmod(0o755)
    _resign_on_mac(platform, [binary, *sorted(out.glob("*.dylib"))])
    return binary


def _resign_on_mac(platform: str, files: list[Path]) -> None:
    """The Tor Project rewrites tor's libevent path to @executable_path after linking, which
    leaves its ad-hoc signature invalid, and Apple Silicon kills a program like that at once.
    The pinned SHA-256 was checked on the download first; this only renews the ad-hoc seal.
    (The app build re-signs every Mach-O in the bundle anyway; this keeps a checkout working.)"""
    if not platform.startswith("macos") or sys.platform != "darwin":
        return
    codesign = shutil.which("codesign") or "/usr/bin/codesign"
    for path in files:
        subprocess.run([codesign, "--force", "--sign", "-", str(path)], check=True, capture_output=True)
        subprocess.run([codesign, "--verify", "--strict", str(path)], check=True, capture_output=True)


def build_wireproxy(platform: str, work: Path) -> Path:
    go = shutil.which("go")
    if not go:
        raise SystemExit("Go is needed to build wireproxy (or pass --wireproxy-file with a pinned build)")
    goos, goarch = _GO_TARGET[platform]
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": goos, "GOARCH": goarch, "GOFLAGS": "-mod=mod",
           "GOPATH": str(work / "gopath"), "GOTOOLCHAIN": "go1.26.0"}
    module_dir = work / "gopath" / "pkg" / "mod" / "github.com" / "windtf" / f"wireproxy@{WIREPROXY_VERSION}"
    subprocess.run([go, "mod", "download", f"{WIREPROXY_MODULE}@{WIREPROXY_VERSION}"], check=True, env=env, cwd=work)
    out = work / f"wireproxy-{platform}"
    subprocess.run([go, "build", "-trimpath", "-ldflags", "-s -w", "-o", str(out), "./cmd/wireproxy"],
                   check=True, env=env, cwd=module_dir)
    return out


def place_wireproxy(platform: str, built: Path, dest: Path) -> Path:
    expected = WIREPROXY_SHA256[platform]
    actual = sha256_file(built)
    if actual != expected:
        raise SystemExit(f"wireproxy for {platform}: SHA-256 {actual} does not match the pinned {expected}")
    out = dest / "third_party" / "wireproxy" / platform
    out.mkdir(parents=True, exist_ok=True)
    target = out / "wireproxy"
    if not (target.exists() and target.resolve() == built.resolve()):
        shutil.copyfile(built, target)
    target.chmod(0o755)
    _resign_on_mac(platform, [target])
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", required=True, choices=sorted(TOR_BUNDLES))
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--wireproxy-file", type=Path, default=None)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="vool-private-web-") as work_text:
        work = Path(work_text)
        tor = place_tor(args.platform, fetch_tor(args.platform, args.cache or work / "cache"), args.dest)
        built = args.wireproxy_file or build_wireproxy(args.platform, work)
        wireproxy = place_wireproxy(args.platform, built, args.dest)
    print(f"tor       {tor}")
    print(f"wireproxy {wireproxy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

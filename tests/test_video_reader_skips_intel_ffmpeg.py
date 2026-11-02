"""On Apple Silicon the video reader never starts an x86_64-only ffmpeg.

An Intel Homebrew ffmpeg (/usr/local/bin) on an arm64 Mac runs under Rosetta, and macOS then shows
"Support Ending for Intel-Based Apps" for VOOL. The reader takes a native or universal binary, and
reports video unavailable when only an Intel one exists. The binaries here are real Mach-O objects
compiled with clang, so the slice reader is checked against the real format.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.artifact_readers import video

CLANG = shutil.which("clang")
LIPO = next((shutil.which(n) for n in ("lipo", "llvm-lipo", *(f"llvm-lipo-{v}" for v in range(22, 13, -1)))
             if shutil.which(n)), None)
pytestmark = pytest.mark.skipif(not (CLANG and LIPO), reason="needs clang and lipo/llvm-lipo to build Mach-O files")


def _macho(tmp: Path, name: str, *archs: str) -> Path:
    src = tmp / "unit.c"
    src.write_text("int f(void) { return 1; }\n")
    objs = []
    for arch in archs:
        out = tmp / f"{name}-{arch}.o"
        subprocess.run([CLANG, "-target", f"{arch}-apple-macos14", "-c", str(src), "-o", str(out)], check=True)
        objs.append(out)
    target = tmp / name
    if len(objs) == 1:
        shutil.copyfile(objs[0], target)
    else:
        subprocess.run([LIPO, "-create", *map(str, objs), "-output", str(target)], check=True)
    target.chmod(0o755)
    return target


def _on_apple_silicon(monkeypatch):
    monkeypatch.setattr(video.sys, "platform", "darwin")
    monkeypatch.setattr(video.platform, "machine", lambda: "arm64")


def test_slices_are_read_from_real_mach_o(tmp_path):
    assert video.macho_archs(str(_macho(tmp_path, "a", "arm64"))) == {"arm64"}
    assert video.macho_archs(str(_macho(tmp_path, "b", "x86_64"))) == {"x86_64"}
    assert video.macho_archs(str(_macho(tmp_path, "c", "arm64", "x86_64"))) == {"arm64", "x86_64"}
    script = tmp_path / "s"
    script.write_text("#!/bin/sh\n")
    assert video.macho_archs(str(script)) is None


def test_an_intel_only_ffmpeg_is_passed_over_for_a_native_one(tmp_path, monkeypatch):
    _on_apple_silicon(monkeypatch)
    intel = _macho(tmp_path, "intel-ffmpeg", "x86_64")
    native = _macho(tmp_path, "native-ffmpeg", "arm64")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert video._which((str(intel), str(native))) == str(native)


def test_a_universal_ffmpeg_is_accepted(tmp_path, monkeypatch):
    _on_apple_silicon(monkeypatch)
    universal = _macho(tmp_path, "ffmpeg", "x86_64", "arm64")
    assert video._which((str(universal),)) == str(universal)


def test_only_an_intel_ffmpeg_means_video_is_unavailable(tmp_path, monkeypatch):
    _on_apple_silicon(monkeypatch)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    intel = _macho(bindir, "ffmpeg", "x86_64")
    monkeypatch.setenv("PATH", str(bindir))
    assert video._which((str(intel),)) == ""


def test_an_intel_mac_keeps_its_intel_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(video.sys, "platform", "darwin")
    monkeypatch.setattr(video.platform, "machine", lambda: "x86_64")
    intel = _macho(tmp_path, "ffmpeg", "x86_64")
    assert video._which((str(intel),)) == str(intel)

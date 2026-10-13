"""The macOS app bundles ollama's model runner, not only the ollama binary.

Intel acceptance run, 2026-10-12 (macos-15-intel, ollama v0.40.3): the DMG bundled the ollama
binary alone. It pulled qwen3:8b, but every chat that needed the model failed inside ollama with
"llama-server binary not found", so recall could never answer. Ollama looks for llama-server next
to its own binary, and the x86_64 runner loads the ggml/llama libraries and CPU backends beside it.

These run the build script's own function under bash against a release directory laid out like
ollama-darwin.tgz, with lipo answered from each file's first line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
BASH = Path("/bin/bash") if Path("/bin/bash").exists() else shutil.which("bash")
RUNNER_MARKER = b"llama-server binary not found"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _function() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = lines.index("bundle_ollama_runtime() {")
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


def _macho(path: Path, *archs: str) -> None:
    path.write_bytes(b"ARCHS " + " ".join(archs).encode() + b"\n")
    path.chmod(0o755)


def _release(root: Path, *, with_runner: bool = True) -> Path:
    """A directory shaped like ollama v0.40.3's ollama-darwin.tgz."""
    src = root / "release"
    src.mkdir()
    (src / "ollama").write_bytes(b"ARCHS x86_64 arm64\n" + RUNNER_MARKER + b"\n")
    if with_runner:
        _macho(src / "llama-server", "x86_64", "arm64")
    _macho(src / "llama-quantize", "x86_64", "arm64")
    _macho(src / "libggml.0.25.3.dylib", "x86_64")
    os.symlink("libggml.0.25.3.dylib", src / "libggml.0.dylib")
    os.symlink("libggml.0.dylib", src / "libggml.dylib")
    _macho(src / "libggml-cpu-haswell.so", "x86_64")
    _macho(src / "libapple-only.dylib", "arm64")
    (src / "LLAMA_CPP_LICENSE").write_text("MIT\n")
    (src / "mlx_metal_v4").mkdir()
    _macho(src / "mlx_metal_v4" / "libmlx.dylib", "arm64")
    return src


def _run(src: Path, dst: Path, arch: str) -> subprocess.CompletedProcess:
    dst.mkdir()
    shutil.copy(src / "ollama", dst / "ollama")
    driver = "\n".join(
        [
            "set -euo pipefail",
            'say() { echo "$@"; }',
            'die() { echo "DIE: $*"; exit 3; }',
            # lipo answers from the fake Mach-O header line and fails on anything else, as the
            # real one fails on a license file.
            'lipo() { local l; IFS= read -r l < "$2" || true; [[ $l == \'ARCHS \'* ]] || return 1; echo "${l#ARCHS }"; }',
            _function(),
            f'bundle_ollama_runtime "{src}" "{dst}" "{arch}"',
            "echo done",
        ]
    )
    return subprocess.run([str(BASH), "-c", driver], capture_output=True, text=True, timeout=60)


def test_an_intel_bundle_carries_the_runner_its_libraries_and_cpu_backends(tmp_path):
    dst = tmp_path / "ollama"
    run = _run(_release(tmp_path), dst, "x86_64")
    assert run.returncode == 0 and "done" in run.stdout, (run.stdout, run.stderr)
    for name in (
        "llama-server",
        "llama-quantize",
        "libggml.0.25.3.dylib",
        "libggml-cpu-haswell.so",
        "LLAMA_CPP_LICENSE",
    ):
        assert (dst / name).is_file() and not (dst / name).is_symlink(), name
    assert os.readlink(dst / "libggml.0.dylib") == "libggml.0.25.3.dylib"
    assert os.readlink(dst / "libggml.dylib") == "libggml.0.dylib"
    assert (dst / "libggml.dylib").resolve() == (dst / "libggml.0.25.3.dylib").resolve()
    # Nothing an Intel Mac cannot load, and not the macOS 26 Apple Silicon MLX engine.
    assert not (dst / "libapple-only.dylib").exists()
    assert not (dst / "mlx_metal_v4").exists()


def test_an_apple_silicon_bundle_leaves_out_intel_only_libraries_and_their_links(tmp_path):
    dst = tmp_path / "ollama"
    run = _run(_release(tmp_path), dst, "arm64")
    assert run.returncode == 0 and "done" in run.stdout, (run.stdout, run.stderr)
    assert (dst / "llama-server").is_file()
    assert (dst / "libapple-only.dylib").is_file()
    for name in ("libggml.0.25.3.dylib", "libggml-cpu-haswell.so", "libggml.0.dylib", "libggml.dylib"):
        assert not os.path.lexists(dst / name), name
    assert "left out libggml.0.25.3.dylib (slices: x86_64)" in run.stdout


def test_an_ollama_that_needs_a_runner_it_cannot_get_fails_the_build(tmp_path):
    run = _run(_release(tmp_path, with_runner=False), tmp_path / "ollama", "x86_64")
    assert run.returncode == 3, (run.stdout, run.stderr)
    assert "DIE: the bundled ollama runs models through llama-server, but no x86_64 llama-server" in run.stdout


def test_a_runner_without_the_target_slice_fails_the_build(tmp_path):
    src = _release(tmp_path)
    _macho(src / "llama-server", "arm64")
    run = _run(src, tmp_path / "ollama", "x86_64")
    assert run.returncode == 3, (run.stdout, run.stderr)
    assert "left out llama-server (slices: arm64)" in run.stdout


def test_an_ollama_with_its_runner_built_in_needs_no_companions(tmp_path):
    src = tmp_path / "release"
    src.mkdir()
    (src / "ollama").write_bytes(b"ARCHS x86_64 arm64\n")
    dst = tmp_path / "ollama"
    run = _run(src, dst, "x86_64")
    assert run.returncode == 0 and "done" in run.stdout, (run.stdout, run.stderr)
    assert sorted(p.name for p in dst.iterdir()) == ["ollama"]

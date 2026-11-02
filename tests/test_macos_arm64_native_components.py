"""An Apple Silicon VOOL.app must not run, carry or start Intel-only code.

macOS shows "Support Ending for Intel-Based Apps" for an app as soon as one of its processes runs
under Rosetta. These pin the two places an x86_64 component could reach the arm64 app:

* a Mach-O inside the bundle without an arm64 slice -- the package job's lipo/file check
  (installer/bundle/macos_slice_check.sh) must fail on it, on real Mach-O files;
* the wrapper app's interpreter, which lives outside the bundle -- an Intel Python imports webview
  fine and runs translated, so the build and the launcher must both refuse it.

The slice tests compile real Mach-O objects with clang and read them with the real `file` and
`lipo` (llvm-lipo answers `-archs` the same way on Linux).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
SLICE_CHECK = REPO / "installer" / "bundle" / "macos_slice_check.sh"
BASH = shutil.which("bash")


def _lipo() -> str | None:
    for name in ("lipo", "llvm-lipo", *(f"llvm-lipo-{v}" for v in range(22, 13, -1))):
        found = shutil.which(name)
        if found:
            return found
    for candidate in sorted(Path("/usr/lib").glob("llvm-*/bin/llvm-lipo"), reverse=True):
        return str(candidate)
    return None


LIPO = _lipo()
CLANG = shutil.which("clang")
FILE = shutil.which("file")
needs_macho_tools = pytest.mark.skipif(
    not (BASH and LIPO and CLANG and FILE),
    reason="needs bash, clang (Mach-O target), lipo or llvm-lipo, and file to build and read real Mach-O files",
)


def _obj(tmp: Path, arch: str) -> Path:
    src = tmp / "unit.c"
    src.write_text("int vool_unit(void) { return 1; }\n", encoding="utf-8")
    out = tmp / f"unit-{arch}.o"
    if not out.exists():
        subprocess.run([CLANG, "-target", f"{arch}-apple-macos14", "-c", str(src), "-o", str(out)], check=True)
    return out


def _universal(tmp: Path) -> Path:
    out = tmp / "unit-universal.o"
    if not out.exists():
        subprocess.run([LIPO, "-create", str(_obj(tmp, "arm64")), str(_obj(tmp, "x86_64")), "-output", str(out)],
                       check=True)
    return out


def _app(tmp: Path, files: dict[str, Path | bytes]) -> Path:
    app = tmp / "VOOL.app"
    for rel, content in files.items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Path):
            shutil.copyfile(content, target)
        else:
            target.write_bytes(content)
    return app


def _check(app: Path, arch: str, **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LIPO": LIPO or "lipo", **env}
    return subprocess.run([BASH, str(SLICE_CHECK), str(app), arch], capture_output=True, text=True,
                          env=full, timeout=120)


@needs_macho_tools
def test_an_arm64_app_with_native_and_universal_mach_o_passes(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    app = _app(tmp_path, {
        "Contents/MacOS/VOOL": _obj(work, "arm64"),
        "Contents/Resources/python/lib/_ext.so": _universal(work),
        "Contents/Resources/app/core/module.py": b"x = 1\n",
    })
    run = _check(app, "arm64")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Mach-O files 2 (universal 1)" in run.stdout and "PASS" in run.stdout


@needs_macho_tools
def test_one_x86_64_only_mach_o_fails_the_arm64_package_and_is_named(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    app = _app(tmp_path, {
        "Contents/MacOS/VOOL": _obj(work, "arm64"),
        "Contents/Resources/ollama/lib/libggml-cpu-haswell.so": _obj(work, "x86_64"),
    })
    run = _check(app, "arm64")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "MISSING-arm64 Contents/Resources/ollama/lib/libggml-cpu-haswell.so: slices [x86_64" in run.stdout
    assert "cannot run natively as arm64" in run.stderr


@needs_macho_tools
def test_an_x86_64_only_file_with_no_extension_or_exec_bit_is_still_found(tmp_path):
    """Classification is by content (file + header magic), never by name or mode."""
    work = tmp_path / "work"; work.mkdir()
    app = _app(tmp_path, {
        "Contents/MacOS/VOOL": _obj(work, "arm64"),
        "Contents/Resources/data/blob": _obj(work, "x86_64"),
    })
    (app / "Contents/Resources/data/blob").chmod(0o644)
    run = _check(app, "arm64")
    assert run.returncode == 1 and "MISSING-arm64 Contents/Resources/data/blob" in run.stdout


@needs_macho_tools
def test_the_intel_package_is_held_to_its_own_slice(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    app = _app(tmp_path, {"Contents/MacOS/VOOL": _obj(work, "arm64")})
    run = _check(app, "x86_64")
    assert run.returncode == 1 and "MISSING-x86_64 Contents/MacOS/VOOL" in run.stdout


@needs_macho_tools
def test_a_bundle_without_any_mach_o_never_passes(tmp_path):
    app = _app(tmp_path, {"Contents/Resources/app/core/module.py": b"x = 1\n"})
    run = _check(app, "arm64")
    assert run.returncode == 1 and "no Mach-O file found" in run.stderr


@needs_macho_tools
def test_a_mach_o_lipo_cannot_read_fails_closed(tmp_path):
    """A truncated file that still carries the Mach-O magic is unreadable, not skipped."""
    work = tmp_path / "work"; work.mkdir()
    app = _app(tmp_path, {
        "Contents/MacOS/VOOL": _obj(work, "arm64"),
        "Contents/Resources/broken.dylib": bytes.fromhex("cffaedfe") + b"\x0c\x00\x00\x01",
    })
    run = _check(app, "arm64")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "Contents/Resources/broken.dylib" in run.stdout


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_a_missing_lipo_fails_closed_instead_of_passing(tmp_path):
    app = _app(tmp_path, {"Contents/Resources/x.py": b"x = 1\n"})
    run = subprocess.run([BASH, str(SLICE_CHECK), str(app), "arm64"], capture_output=True, text=True,
                         env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LIPO": "no-such-lipo-tool"},
                         timeout=60)
    assert run.returncode == 1 and "no-such-lipo-tool is not available" in run.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_an_unknown_target_architecture_is_refused(tmp_path):
    app = _app(tmp_path, {"Contents/Resources/x.py": b"x = 1\n"})
    run = subprocess.run([BASH, str(SLICE_CHECK), str(app), "i386"], capture_output=True, text=True, timeout=60)
    assert run.returncode == 1 and "unsupported target architecture: i386" in run.stderr


# ------------------------------------------------------------------------------------------
# The wrapper app's interpreter: outside the bundle, so no slice check can see it
# ------------------------------------------------------------------------------------------


def _interpreter(directory: Path, name: str, *, webview: bool, machine: str) -> Path:
    """A stand-in interpreter answering the two questions the resolver asks: can it import
    webview, and which architecture does platform.machine() report (x86_64 under Rosetta)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ] && [ "$2" = "import webview" ]; then\n'
        f"  exit {'0' if webview else '1'}\n"
        "fi\n"
        'case "$2" in\n'
        f"  *platform.machine*) case \"$2\" in *\"'{machine}'\"*) exit 0 ;; *) exit 1 ;; esac ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _build_selector() -> str:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    capable = re.search(r"^webview_capable\(\)[^\n]*\n", text, re.MULTILINE)
    select = re.search(r"^select_capable_wrapper_python\(\) \{\n.*?^\}$", text, re.MULTILINE | re.DOTALL)
    assert capable and select, "the build script lost its wrapper interpreter selector"
    return capable.group(0) + select.group(0)


def _launcher_resolver(target_arch: str) -> str:
    """The wrapper launcher's resolver, as the build EMITS it (the heredoc expanded for one arch)."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"(py_ok\(\) \{.*?\nfi)\n\n# The long-lived native host owns", text, re.DOTALL)
    assert match, "the wrapper launcher lost its interpreter resolver"
    return match.group(1).replace("${TARGET_ARCH}", target_arch).replace("\\$", "$")


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_build_refuses_an_intel_interpreter_for_an_arm64_wrapper(tmp_path):
    root = tmp_path / "install"
    _interpreter(root / ".venv" / "bin", "python", webview=True, machine="x86_64")
    pythons = tmp_path / "bin"
    _interpreter(pythons, "python3", webview=True, machine="x86_64")
    script = _build_selector() + '\nTARGET_ARCH=arm64\nselect_capable_wrapper_python "$ROOT" && echo || echo NONE'
    run = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         env={"PATH": str(pythons), "ROOT": str(root)})
    assert run.stdout.strip() == "NONE", run.stdout + run.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_build_passes_over_an_intel_venv_to_a_native_interpreter(tmp_path):
    root = tmp_path / "install"
    _interpreter(root / ".venv" / "bin", "python", webview=True, machine="x86_64")
    pythons = tmp_path / "bin"
    native = _interpreter(pythons, "python3.12", webview=True, machine="arm64")
    script = _build_selector() + '\nTARGET_ARCH=arm64\nselect_capable_wrapper_python "$ROOT"'
    run = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         env={"PATH": str(pythons), "ROOT": str(root)})
    assert run.stdout == str(native), run.stdout + run.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_an_intel_build_still_takes_its_own_native_interpreter(tmp_path):
    root = tmp_path / "install"
    venv_python = _interpreter(root / ".venv" / "bin", "python", webview=True, machine="x86_64")
    script = _build_selector() + '\nTARGET_ARCH=x86_64\nselect_capable_wrapper_python "$ROOT"'
    run = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         env={"PATH": str(tmp_path / "empty"), "ROOT": str(root)})
    assert run.stdout == str(venv_python), run.stdout + run.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_launcher_skips_an_intel_interpreter_on_an_arm64_app(tmp_path):
    root = tmp_path / "install"
    _interpreter(root / ".venv" / "bin", "python", webview=True, machine="x86_64")
    pythons = tmp_path / "bin"
    _interpreter(pythons, "python3.13", webview=True, machine="x86_64")
    native = _interpreter(pythons, "python3", webview=True, machine="arm64")
    run = subprocess.run([BASH, "-c", _launcher_resolver("arm64") + '\nprintf "PICKED=%s" "${PY:-UNSET}"'],
                         capture_output=True, text=True, timeout=60,
                         env={"PATH": str(pythons), "PROJECT_ROOT": str(root), "HOME": str(tmp_path)})
    assert run.stdout == f"PICKED={native}", run.stdout + run.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_launcher_refuses_to_start_when_only_intel_interpreters_exist(tmp_path):
    root = tmp_path / "install"
    _interpreter(root / ".venv" / "bin", "python", webview=True, machine="x86_64")
    run = subprocess.run([BASH, "-c", _launcher_resolver("arm64") + '\nprintf "PICKED=%s" "${PY:-UNSET}"'],
                         capture_output=True, text=True, timeout=60,
                         env={"PATH": str(tmp_path / "empty"), "PROJECT_ROOT": str(root), "HOME": str(tmp_path)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert "runs natively as arm64" in run.stdout

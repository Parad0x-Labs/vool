"""The arm64 VOOL.app ships no Intel code inside its embedded Python.

pyobjc 13's wheels are universal2: objc/_objc, _machsignals, the EventKit and Contacts extensions and
_objc's dSYM each carried an x86_64 slice beside the arm64 one in the 0.8 arm64 package (Mac check on
247124ed). These run the build's own ``thin_embedded_python`` against a staged tree, with a lipo
double that reads the slice list from a header line, as the ollama bundling tests do.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
BASH = Path("/bin/bash") if Path("/bin/bash").exists() else shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _function() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = lines.index("thin_embedded_python() {")
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


def _macho(path: Path, *archs: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ARCHS " + " ".join(archs).encode() + b"\nbody\n")
    path.chmod(mode)
    return path


def _tree(root: Path) -> Path:
    py = root / "python"
    sp = py / "lib" / "python3.11" / "site-packages"
    _macho(sp / "objc" / "_objc.cpython-311-darwin.so", "x86_64", "arm64", mode=0o755)
    _macho(sp / "objc" / "_machsignals.cpython-311-darwin.so", "x86_64", "arm64")
    _macho(sp / "Contacts" / "_Contacts.cpython-311-darwin.so", "x86_64", "arm64")
    _macho(sp / "EventKit" / "_EventKit.cpython-311-darwin.so", "x86_64", "arm64")
    _macho(
        sp
        / "objc"
        / "_objc.cpython-311-darwin.so.dSYM"
        / "Contents"
        / "Resources"
        / "DWARF"
        / "_objc.cpython-311-darwin.so",
        "x86_64",
        "arm64",
    )
    _macho(sp / "pydantic_core" / "_pydantic_core.cpython-311-darwin.so", "arm64")
    _macho(py / "bin" / "python3.11", "arm64", mode=0o755)
    _macho(sp / "intel_only" / "_ext.so", "x86_64")
    (sp / "objc" / "__init__.py").write_text("import sys\n")
    (py / "bin" / "pip").write_text("#!/bin/sh\n")
    (py / "bin" / "pip").chmod(0o755)
    return py


def _run(py: Path, arch: str, *, thin_fails: bool = False) -> subprocess.CompletedProcess:
    driver = "\n".join(
        [
            "set -euo pipefail",
            'say() { echo "$@"; }',
            'die() { echo "DIE: $*"; exit 3; }',
            'lipo() { local l; if [[ $1 == -thin ]]; then IFS= read -r l < "$5" || return 1;'
            ' [[ " ${l#ARCHS } " == *" $2 "* ]] || return 1; { echo "ARCHS $2"; tail -n +2 "$5"; } > "$4"; return; fi;'
            ' IFS= read -r l < "$2" || true; [[ $l == \'ARCHS \'* ]] || return 1; echo "${l#ARCHS }"; }',
            *(
                [
                    "real_lipo() { :; }",
                    'eval "real_$(declare -f lipo)"',
                    'lipo() { [[ $1 == -thin ]] && return 1; real_lipo "$@"; }',
                ]
                if thin_fails
                else []
            ),
            _function(),
            f'thin_embedded_python "{py}" "{arch}"',
            "echo done",
        ]
    )
    return subprocess.run([str(BASH), "-c", driver], capture_output=True, text=True, timeout=60)


def _slices(path: Path) -> str:
    return path.read_bytes().split(b"\n", 1)[0].decode()


def test_every_universal_file_in_the_embedded_python_keeps_only_the_arm64_slice(tmp_path):
    py = _tree(tmp_path)
    run = _run(py, "arm64")
    assert run.returncode == 0 and "done" in run.stdout, (run.stdout, run.stderr)
    sp = py / "lib" / "python3.11" / "site-packages"
    for rel in (
        "objc/_objc.cpython-311-darwin.so",
        "objc/_machsignals.cpython-311-darwin.so",
        "Contacts/_Contacts.cpython-311-darwin.so",
        "EventKit/_EventKit.cpython-311-darwin.so",
        "objc/_objc.cpython-311-darwin.so.dSYM/Contents/Resources/DWARF/_objc.cpython-311-darwin.so",
    ):
        assert _slices(sp / rel) == "ARCHS arm64", rel
        assert (sp / rel).read_bytes().endswith(b"body\n"), rel
    assert "5 universal file(s) in the embedded Python thinned to arm64" in run.stdout
    # Mode is kept: the file is rewritten in place, not replaced.
    assert (sp / "objc" / "_objc.cpython-311-darwin.so").stat().st_mode & 0o777 == 0o755
    assert not list(py.rglob("*.thin"))


def test_native_files_scripts_and_sources_are_left_untouched(tmp_path):
    py = _tree(tmp_path)
    run = _run(py, "arm64")
    assert run.returncode == 0, (run.stdout, run.stderr)
    sp = py / "lib" / "python3.11" / "site-packages"
    assert _slices(sp / "pydantic_core" / "_pydantic_core.cpython-311-darwin.so") == "ARCHS arm64"
    assert _slices(py / "bin" / "python3.11") == "ARCHS arm64"
    assert (py / "bin" / "pip").read_text() == "#!/bin/sh\n"
    assert (sp / "objc" / "__init__.py").read_text() == "import sys\n"
    assert "pydantic_core" not in run.stdout


def test_a_file_without_the_target_slice_is_left_for_the_architecture_gate(tmp_path):
    py = _tree(tmp_path)
    run = _run(py, "arm64")
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert _slices(py / "lib" / "python3.11" / "site-packages" / "intel_only" / "_ext.so") == "ARCHS x86_64"


def test_the_intel_package_keeps_only_its_own_slice(tmp_path):
    py = _tree(tmp_path)
    run = _run(py, "x86_64")
    assert run.returncode == 0, (run.stdout, run.stderr)
    sp = py / "lib" / "python3.11" / "site-packages"
    assert _slices(sp / "Contacts" / "_Contacts.cpython-311-darwin.so") == "ARCHS x86_64"


def test_a_universal_file_that_cannot_be_thinned_fails_the_build(tmp_path):
    py = _tree(tmp_path)
    run = _run(py, "arm64", thin_fails=True)
    assert run.returncode == 3
    assert "DIE: could not thin" in run.stdout and "to arm64" in run.stdout


def test_the_build_thins_after_the_lock_drift_check_and_before_bytecode_and_signing():
    text = SCRIPT.read_text(encoding="utf-8")
    drift = text.index('die "the installed dependencies differ from uv.lock')
    call = text.index('thin_embedded_python "${res}/python" "${TARGET_ARCH}"')
    bytecode = text.index("[2b/4] deterministic bytecode")
    signing = text.index("all ${seen_macho} Mach-O files in Contents/Resources carry a valid signature")
    assert drift < call < bytecode < signing

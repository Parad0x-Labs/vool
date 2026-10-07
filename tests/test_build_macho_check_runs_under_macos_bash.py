"""The bundle's Mach-O signature check runs under the bash that macOS ships (3.2).

Release dry run, 2026-10-07: the check fed its Mach-O lister to a process substitution through a
here-document. bash 3.2 fails that at run time ("ambiguous redirect"), the lister never ran, nothing was
enumerated, and every self-contained release build died at "found no Mach-O files". `bash -n` does not
catch it, so this runs the script's own section under /bin/bash against a bundle with one real Mach-O.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
MAC_BASH = Path("/bin/bash")
REAL_MACHO = Path("/bin/ls")

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not MAC_BASH.exists(), reason="macOS /bin/bash")


def _signature_section() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "bad_macho=0")
    end = next(i for i in range(start, len(lines)) if '"${seen_macho}" -gt 0' in lines[i])
    return "\n".join(lines[start:end + 1])


def test_the_signature_check_enumerates_the_bundles_mach_o_files_under_macos_bash(tmp_path):
    resources = tmp_path / "VOOL.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    shutil.copy(REAL_MACHO, resources / "tool")
    (resources / "notes.txt").write_text("not a binary")
    driver = "\n".join([
        "set -u",
        'say() { echo "$@"; }',
        'die() { echo "DIE: $*"; exit 3; }',
        "codesign() { return 0; }",  # signing is not under test; the enumeration is
        f'APP="{tmp_path / "VOOL.app"}"',
        f'SYS_PY="{sys.executable}"',
        _signature_section(),
        'echo "seen=${seen_macho} bad=${bad_macho}"',
    ])
    run = subprocess.run([str(MAC_BASH), "-c", driver], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert "seen=1 bad=0" in run.stdout, (run.stdout, run.stderr)

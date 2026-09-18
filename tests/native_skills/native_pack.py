#!/usr/bin/env python3
"""The native skill library's bounded cumulative pack.

Runs the whole `tests/native_skills/` suite (library contract, selection law, served workflows)
plus the one pre-existing order pin the native seam extended. Bounded by construction: this is
the pack a package edit must pass before it ships — not the 24k-test full suite.

    /tmp/vool-venv312/bin/python tests/native_skills/native_pack.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PACK_SELECTION = [
    "tests/native_skills",
    "tests/test_a_staged_skill_is_found_by_the_name_create_returned.py",
    "tests/test_plugin_skills.py",
    "tests/test_installed_skill_becomes_visible.py",
]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    command = [sys.executable, "-m", "pytest", *PACK_SELECTION, "-q"]
    print(f"native-skill pack: {' '.join(command)}")
    print(f"root: {repo_root}")
    return subprocess.run(command, cwd=str(repo_root)).returncode


if __name__ == "__main__":
    raise SystemExit(main())

"""The disposable fixture repository for the coding-assistant journey.

A REAL git repository with a REAL defect: ``stats.py::median`` indexes the midpoint of a list it
never sorted. The narrow test fails at base (the reproduction), the fix is a one-line sort, and a
second clean module keeps the cumulative pack honest — a fix that silences the narrow test by
breaking its neighbour must not pass. Every constant here is byte-stable so tests can prove exact
file bytes before and after the fix, and after rollback.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

DEFECT_STATS_PY = '''"""Tiny statistics helpers (fixture repo)."""


def median(values):
    """Return the median value. DEFECT: the input is indexed without sorting first,
    so any unsorted input returns the wrong element."""
    return values[len(values) // 2]


def mean(values):
    return sum(values) / len(values)
'''

FIXED_STATS_PY = '''"""Tiny statistics helpers (fixture repo)."""


def median(values):
    """Return the median value. The input is sorted before indexing, so the
    midpoint is the true median for any ordering of the input."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def mean(values):
    return sum(values) / len(values)
'''

# The exact old_text/new_text pair the scripted model proposes for workspace.replace_in_file.
# Replacing docstring and body in ONE edit is what makes FIXED_STATS_PY the byte-exact outcome.
DEFECT_OLD_TEXT = (
    '    """Return the median value. DEFECT: the input is indexed without sorting first,\n'
    '    so any unsorted input returns the wrong element."""\n'
    "    return values[len(values) // 2]"
)
FIX_NEW_TEXT = (
    '    """Return the median value. The input is sorted before indexing, so the\n'
    '    midpoint is the true median for any ordering of the input."""\n'
    "    ordered = sorted(values)\n"
    "    return ordered[len(ordered) // 2]"
)

TEST_STATS_PY = '''from stats import median


def test_median_unsorted_input():
    # The defect: median of an unsorted list indexes the raw midpoint.
    assert median([5, 1, 3]) == 3


def test_median_sorted_input():
    assert median([1, 3, 5]) == 3
'''

TEST_TEXTUTIL_PY = '''from textutil import shout


def test_shout():
    assert shout("hey") == "HEY!"
'''

TEXTUTIL_PY = '''"""Text helpers (fixture repo) — the clean neighbour the regression pack guards."""


def shout(text):
    return text.upper() + "!"
'''

NARROW_TEST_COMMAND = "python3 -m pytest -q tests/test_stats.py"
REGRESSION_COMMAND = "python3 -m pytest -q"

OWNER_PATH = "stats.py"


def build_fixture_repo(parent: Path, *, name: str = "fixture-repo") -> Path:
    """Create the disposable repository and return its root. Initial commit contains the defect."""
    root = Path(parent) / name
    if root.exists():
        raise FileExistsError(f"fixture repo already exists at {root}")
    root.mkdir(parents=True)
    (root / OWNER_PATH).write_text(DEFECT_STATS_PY, encoding="utf-8")
    (root / "textutil.py").write_text(TEXTUTIL_PY, encoding="utf-8")
    # A root conftest makes pytest put the repo root on sys.path so the tests can
    # `import stats` / `import textutil` without a packaging dance.
    (root / "conftest.py").write_text("# fixture repo: repo root on sys.path\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_stats.py").write_text(TEST_STATS_PY, encoding="utf-8")
    (tests / "test_textutil.py").write_text(TEST_TEXTUTIL_PY, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(
        root,
        "-c", "user.name=fixture-author",
        "-c", "user.email=fixture@example.invalid",
        "commit", "-q", "-m", "fixture: initial state with median defect",
    )
    return root


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


__all__ = [
    "DEFECT_OLD_TEXT",
    "DEFECT_STATS_PY",
    "FIXED_STATS_PY",
    "FIX_NEW_TEXT",
    "NARROW_TEST_COMMAND",
    "OWNER_PATH",
    "REGRESSION_COMMAND",
    "build_fixture_repo",
]

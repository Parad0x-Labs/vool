"""A new instrument under `ops/` may never be invisible.

`.gitignore:98` is `ops/*` with per-file negations — an allowlist, deliberately, so deployment IPs
and SSH paths cannot be committed by accident. `.gitignore:116` states the cost in its own words:

    "a new tool that is not listed is invisible to `git status` and one `git clean` from gone --
     which is what rule 0.9 exists to stop, and what happened when this directory was first added."

It states it and nothing enforced it. Verified 2026-08-29: a fresh `ops/_probe_new_tool.py` is
matched by `ops/*`, shows nothing in `git status`, and would be destroyed by `git clean` without a
word. Existing files are safe only because git does not ignore what it already tracks — which is
protection by accident, not by design.

This test converts that silence into a loud failure: any instrument on disk under `ops/` that git
is neither tracking nor showing must be named here, or given a negation in `.gitignore`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Extensions that are instruments — code and the fixtures instruments read.
_INSTRUMENT_SUFFIXES = {".py", ".sh", ".json", ".md", ".toml", ".yaml", ".yml"}

#: Directories under ops/ that hold RUN OUTPUT rather than instruments. Output is meant to be
#: ignored; if one of these ever starts holding code, add it to the allowlist instead.
_OUTPUT_DIRS = {"__pycache__", "out", "output", "runs", "logs", "results", "artifacts", ".pytest_cache"}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout


def _tracked_ops_files() -> set[str]:
    return {line.strip() for line in _git("ls-files", "ops/").splitlines() if line.strip()}


def _instruments_on_disk() -> set[str]:
    ops = REPO_ROOT / "ops"
    if not ops.is_dir():
        return set()
    found = set()
    for path in ops.rglob("*"):
        if not path.is_file() or path.suffix not in _INSTRUMENT_SUFFIXES:
            continue
        if any(part in _OUTPUT_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        found.add(str(path.relative_to(REPO_ROOT)))
    return found


def test_every_instrument_under_ops_is_tracked_by_git() -> None:
    """An untracked instrument is one `git clean` from gone and invisible in `git status`."""
    untracked = sorted(_instruments_on_disk() - _tracked_ops_files())
    assert not untracked, (
        "these ops instruments exist on disk but git is not tracking them, and `ops/*` hides them "
        f"from `git status`: {untracked}. Add a `!<path>` negation to .gitignore and commit them — "
        "an instrument that is not in git did not happen: it cannot be re-run, cited, or inherited"
    )


def test_the_allowlist_still_hides_a_would_be_secret_bearing_file() -> None:
    """The guard above must not become an argument for un-ignoring the directory.

    `ops/*` exists because these scripts carry deployment IPs and SSH paths. A path that is NOT a
    tracked instrument must still be ignored, so accidental output or a scratch file stays out.
    """
    probe = "ops/_gitignore_probe_scratch.log"
    matched = _git("check-ignore", "-v", probe).strip()
    assert matched, (
        f"{probe} is no longer ignored — the ops allowlist has been weakened, and scratch files "
        "or deployment configs could now be committed by accident"
    )


@pytest.mark.parametrize(
    "instrument",
    ["ops/verify.py", "ops/battery_20_probe.py", "ops/pytest_shards.py"],
)
def test_the_load_bearing_gates_are_tracked(instrument: str) -> None:
    """Named explicitly: these are cited as evidence in state docs and must never go missing."""
    if not (REPO_ROOT / instrument).exists():
        pytest.skip(f"{instrument} is not present in this tree")
    assert instrument in _tracked_ops_files(), f"{instrument} is not tracked"

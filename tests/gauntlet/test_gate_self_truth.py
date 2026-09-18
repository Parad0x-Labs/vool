"""The gate's assertion about itself: required scenarios may not be hidden behind skips.

A release gate that can be quieted by adding `@pytest.mark.skip` is not a gate. This suite counts
the skip markers in the required gauntlet files and fails if the number moves, so silencing a
scenario is a visible, deliberate edit to this number rather than a line nobody reads.

It is deliberately a source-level count rather than a runtime one: a runtime skip count only sees
the scenarios that were collected in the run being measured, so a `-k` selection or a collection
error could report zero while the file is full of them.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

GAUNTLET_DIR = Path(__file__).resolve().parent

# The files that constitute the release gate. `_live.py` and the live-model suites are excluded on
# purpose: they are opt-in and skip by design when no live model is available.
REQUIRED_GATE_FILES = (
    "test_release_conversation_gauntlet.py",
    "test_public_cross_chat_isolation.py",
    "test_harness_control.py",
)
# This file is deliberately NOT in the list: it contains no scenarios, and its own skip-detecting
# pattern would match itself.

# Every required scenario runs. Raising this number is how a scenario gets silenced, so it has to be
# done here, in one line, with a reviewer looking at it.
EXPECTED_REQUIRED_SKIPS = 0

_SKIP_MARKER = re.compile(r"@pytest\.mark\.skip\b|pytest\.skip\(")


def test_no_required_scenario_is_skipped() -> None:
    offenders: dict[str, int] = {}
    for name in REQUIRED_GATE_FILES:
        path = GAUNTLET_DIR / name
        assert path.exists(), f"required gate file missing: {name}"
        count = len(_SKIP_MARKER.findall(path.read_text(encoding="utf-8")))
        if count:
            offenders[name] = count
    total = sum(offenders.values())
    assert total == EXPECTED_REQUIRED_SKIPS, (
        f"required skip count is {total}, expected {EXPECTED_REQUIRED_SKIPS}: {offenders}. "
        "A skipped scenario proves nothing; either fix it, mark it xfail(strict=True) with the "
        "measured defect, or raise EXPECTED_REQUIRED_SKIPS deliberately."
    )


def test_expected_red_scenarios_are_strict() -> None:
    """An `xfail` that is not strict silently passes once the defect is fixed.

    Every EXPECTED-RED scenario in this gate reproduces a measured release defect, so each one must
    be `strict=True`: when the defect is fixed the test XPASSes, strict turns that into a failure,
    and somebody has to come back and remove the marker. A non-strict xfail would let a fixed defect
    sit unnoticed and a re-broken one look identical.
    """
    source = (GAUNTLET_DIR / "test_release_conversation_gauntlet.py").read_text(encoding="utf-8")
    markers = re.findall(r"@pytest\.mark\.xfail\((.*?)\n\)", source, re.DOTALL)
    assert markers, "no xfail markers found -- did the file move?"
    loose = [m.strip()[:80] for m in markers if "strict=True" not in m]
    assert not loose, f"{len(loose)} xfail marker(s) are not strict: {loose}"


# ------------------------------------------------------- no parent-side runtime bootstrap


# Product scenarios execute in the hermetic child. These parent-side files must therefore never
# construct RuntimeServices, never install the harness seams, and never even IMPORT the harness --
# importing it is how a parent-side bootstrap gets reintroduced by accident.
PARENT_ONLY_FILES = (
    "test_release_conversation_gauntlet.py",
    "test_public_cross_chat_isolation.py",
    "test_harness_control.py",
    "test_hermetic_boundary.py",
    "test_gate_self_truth.py",
)

_FORBIDDEN_CALLS = {"runtime", "install", "install_unmanaged", "bootstrap_runtime_services"}


def _forbidden_parent_bootstrap(path: Path) -> list[str]:
    """AST, not grep: an alias or an indirect import would walk straight past a text search.

    Catches three shapes -- importing the harness module at all (under any alias), calling
    `<anything>.runtime()/install()/install_unmanaged()`, and calling `bootstrap_runtime_services()`
    however it was imported.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tests.gauntlet"):
            for alias in node.names:
                if alias.name == "harness":
                    problems.append(f"imports the harness (as {alias.asname or alias.name})")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("gauntlet.harness"):
                    problems.append(f"imports {alias.name}")
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _FORBIDDEN_CALLS:
                problems.append(f"calls {name}() at line {node.lineno}")
    return problems


def test_no_required_gauntlet_file_bootstraps_the_runtime_in_the_parent() -> None:
    offenders: dict[str, list[str]] = {}
    for name in PARENT_ONLY_FILES:
        path = GAUNTLET_DIR / name
        if not path.exists():
            continue
        found = _forbidden_parent_bootstrap(path)
        if found:
            offenders[name] = found
    assert not offenders, (
        f"parent-side runtime bootstrap reintroduced: {offenders}. Product scenarios must run "
        "through tests.gauntlet.hermetic.runner.run_group, in a child process -- bootstrap has no "
        "teardown and poisons the parent interpreter."
    )


def test_the_guard_would_actually_catch_an_aliased_reintroduction(tmp_path: Path) -> None:
    """The guard guards itself: a grep for 'H.runtime' would miss every line below."""
    sneaky = tmp_path / "test_sneaky.py"
    sneaky.write_text(
        "from tests.gauntlet import harness as _hidden\n"
        "def test_x():\n"
        "    _hidden.runtime()\n",
        encoding="utf-8",
    )
    found = _forbidden_parent_bootstrap(sneaky)
    assert any("harness" in f for f in found), found
    assert any("runtime()" in f for f in found), found

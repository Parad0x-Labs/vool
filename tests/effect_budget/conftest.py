"""One isolated budget store per test, and a clean process-instance state.

The store is the runtime sqlite default path re-pointed at a per-test tmp
file (the journal tests' own isolation law). Threads inside a test open
their own connections to the same file, which is exactly the deployment
shape the atomicity proofs run against.
"""
from __future__ import annotations

import os

import pytest

# The package's two autouse fixtures are exported on purpose, underscores and all. pytest applies a
# conftest's autouse fixtures by the identity of its directory's collection node: when one run's
# arguments leave this package for a file of the parent directory and then come back, the package is
# collected again as a new node and these fixtures silently stop applying (measured: the per-test
# store and the real-home guard both absent, every test sharing the session database). A test module
# that star-imports this file (the package convention) registers them on its own module node, which
# no argument order can drop.
__all__ = [
    "_isolated_budget_store",
    "_real_home_residue_guard",
    "operator_home",
    "operator_token",
    "os",
    "pytest",
    "set_budget",
    "synthetic_machine_home",
]


@pytest.fixture(autouse=True)
def _isolated_budget_store(tmp_path, monkeypatch):
    from storage.db import configure_default_db_path

    # Harness repair (Goal 2 stage 2, 2026-09-17, on the frozen FINAL_BASE): the store used to sit
    # directly in tmp_path, which made the runtime-state protection's database-directory root
    # (core.runtime_state_protection.protected_state_roots appends the active db file's parent) the
    # WHOLE test directory — every machine home and workspace path in this suite became
    # "protected_runtime_state", so the budget/machine assertions measured the protection's refusal
    # instead of the budget's (7 failures reproduced at FINAL_BASE before this repair). The
    # production protection is correct: in a real install the db's parent is the runtime's own data
    # directory. Keeping the db in its own subdirectory preserves exactly that production shape —
    # the db directory stays protected (exercising the custom-location root), while the suite's
    # synthetic machine homes and workspaces are siblings of that directory, not inside it.
    store_dir = tmp_path / "runtime-store"
    store_dir.mkdir(parents=True, exist_ok=True)
    configure_default_db_path(str(store_dir / "effect_budget.db"))
    from core import effect_budget

    effect_budget.reset_effect_budget_process_state()
    yield
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)


@pytest.fixture()
def operator_token():
    """A durably-granted operator token, minted OUTSIDE any effect scope."""
    from core import effect_budget

    return effect_budget.grant_operator_budget_authority("test fixture grant")


@pytest.fixture()
def set_budget(operator_token):
    """Set one rule (or several) as the operator."""

    from core import effect_budget

    def _set(*rules: tuple) -> None:
        adjustments = []
        for rule in rules:
            budget_class, scope, limit = rule[0], rule[1], rule[2]
            window = float(rule[3]) if len(rule) > 3 else 0.0
            adjustments.append(
                effect_budget.BudgetAdjustment(
                    budget_class=budget_class,
                    scope=scope,
                    new_limit=limit,
                    window_seconds=window,
                    note="test",
                )
            )
        effect_budget.apply_operator_adjustment(operator_token, adjustments)

    return _set


# ---------------------------------------------------------------------------
# Machine-lane home isolation
#
# The machine write lane targets the operator's REAL Desktop/Downloads/Documents
# by computing them from ``Path.home()``. A test that exercises that lane must
# therefore move HOME itself — patching ``_safe_machine_roots`` or
# ``_machine_home`` instead would replace the confinement authority with a
# double, and the proof would no longer cover the root computation that makes
# the refusal attributable to the budget rather than to confinement.
#
# ``VOOL_HOME`` (which the root conftest sets) does NOT move ``Path.home()``;
# only the HOME environment variable does. That gap is what let
# ``cp2-machine-note.txt`` land on the operator's real Desktop.
# ---------------------------------------------------------------------------

_REAL_HOME_LANE_DIRS = ("Desktop", "Downloads", "Documents")


def operator_home():
    """The operator's real home, resolved from the password database.

    Never from ``HOME``: a test that has already redirected HOME must still be
    measured against the real home, or the residue guard would grade the
    synthetic home and pass vacuously.

    ``VOOL_TEST_OPERATOR_HOME_OVERRIDE`` re-points ONLY this resolution, and
    exists for one purpose: so the guard can be proved to bite (see
    ``test_machine_home_isolation.py``) without a test writing into the real
    home to demonstrate it. It never disables the guard — it moves which
    directory the guard watches.
    """
    import os
    import pwd
    from pathlib import Path

    override = os.environ.get("VOOL_TEST_OPERATOR_HOME_OVERRIDE")
    if override:
        return Path(override)
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _real_home_snapshot() -> dict:
    """Names directly inside the operator's real machine-lane directories."""
    from pathlib import Path  # noqa: F401 - Path is used via operator_home()

    real_home = operator_home()
    snapshot: dict[str, frozenset] = {}
    for name in _REAL_HOME_LANE_DIRS:
        target = real_home / name
        try:
            snapshot[name] = frozenset(p.name for p in target.iterdir())
        except OSError:
            snapshot[name] = frozenset()
    return snapshot


@pytest.fixture(autouse=True)
def _real_home_residue_guard():
    """No test in this package may leave a byte in the operator's real home.

    Autouse and yield-based, so the comparison runs in teardown — which pytest
    executes when the test PASSES, when it FAILS, when it ERRORS, and when the
    run is interrupted (KeyboardInterrupt unwinds through the generator's
    ``finally``). A leak is therefore reported by this guard even if the test
    body never reached its own cleanup.
    """
    before = _real_home_snapshot()
    try:
        yield
    finally:
        after = _real_home_snapshot()
        leaked = {
            name: sorted(after[name] - before[name])
            for name in _REAL_HOME_LANE_DIRS
            if after[name] - before[name]
        }
        assert not leaked, (
            "a test wrote into the operator's REAL home: "
            f"{leaked}. The machine lane must run against a synthetic home "
            "(see the synthetic_machine_home fixture)."
        )


@pytest.fixture()
def synthetic_machine_home(tmp_path, monkeypatch):
    """A throwaway HOME whose machine-lane directories are real directories.

    Returns the synthetic home. ``monkeypatch`` restores HOME in teardown, so
    the redirect is undone on pass, fail, error and interrupt alike.
    """
    import os
    from pathlib import Path

    home = tmp_path / "synthetic-home"
    for name in _REAL_HOME_LANE_DIRS:
        (home / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    # A redirect that silently did not take would make every assertion below
    # read the developer's real machine and fail somewhere far less legible
    # than here. Check every seam the production lane actually consults.
    assert Path.home() == home, "HOME redirect did not take for Path.home()"
    assert Path(os.path.expanduser("~/Desktop")) == home / "Desktop", (
        "HOME redirect did not take for expanduser"
    )
    from core import runtime_execution_tools as _ret

    assert _ret._machine_home() == home, "HOME redirect did not reach _machine_home()"
    roots = tuple(Path(r) for r in _ret._safe_machine_roots())
    assert roots and all(home in r.parents or r == home for r in roots), (
        f"the production machine roots still point outside the synthetic home: {roots}"
    )
    return home

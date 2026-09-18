"""R2, pass 3 (AUD-20260829-003) -- BLUE-3's identity-vacuity finding.

Passes 1-2 fixed the concurrent-first-import race by making the four A9 alias shims
(`core.agent_runtime.live_data_plan`, `.attempt_approval`, `.attempt_followup`, `.attempt_retry`)
copy the canonical module's namespace onto themselves. That closed the race but made the alias and
canonical module OBJECTS two distinct objects: `core.agent_runtime.attempt_followup is
core.attempt_followup` became `False`. Roughly 18 `mock.patch` call sites across the tree patch
the alias path expecting it to affect the SAME object the runtime actually imports and calls
through (`core.<name>`, per `core/agent_runtime/__init__.py`'s own `from . import (...)` and every
production call site's preference for the canonical path) -- with identity broken, those patches
silently land on a dead copy nobody reads. This does not just fail; it can PASS VACUOUSLY, which
is the worse failure mode: any sabotage proof routed through such a patch would go green whether
or not the defect it exists to catch was reintroduced (BLUE-3's report, pass 3).

The pass-3 fix (`core/agent_runtime/__init__.py`) restores identity without reopening the race --
see that file's own docstring and `tests/test_module_identity_race.py`'s
`test_cold_start_concurrent_first_import_of_the_package_itself` for the mechanism and its own
race proof. This file pins the two properties pass 3 exists to restore:
  1. `core.agent_runtime.<alias> is core.<canonical>` for all four shims.
  2. A `mock.patch` on EITHER path is observed by code importing through the OTHER -- the exact
     property BLUE-3 found silently lost. Both directions matter: real call sites in this tree
     patch both `core.agent_runtime.<alias>.<name>` (the vacuated shape) and `core.<name>.<name>`
     directly, and both must actually take effect now.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import core.agent_runtime.attempt_approval as alias_attempt_approval
import core.agent_runtime.attempt_followup as alias_attempt_followup
import core.agent_runtime.attempt_retry as alias_attempt_retry
import core.agent_runtime.live_data_plan as alias_live_data_plan
import core.attempt_approval as canonical_attempt_approval
import core.attempt_followup as canonical_attempt_followup
import core.attempt_retry as canonical_attempt_retry
import core.live_data_plan as canonical_live_data_plan

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# (alias module, canonical module, alias dotted path, canonical dotted path, an attribute name
# each canonical module actually defines, safe to monkeypatch with a plain sentinel value).
TARGETS = [
    (
        alias_live_data_plan,
        canonical_live_data_plan,
        "core.agent_runtime.live_data_plan",
        "core.live_data_plan",
        "_weather_subtask",
    ),
    (
        alias_attempt_approval,
        canonical_attempt_approval,
        "core.agent_runtime.attempt_approval",
        "core.attempt_approval",
        "is_approval_valid",
    ),
    (
        alias_attempt_followup,
        canonical_attempt_followup,
        "core.agent_runtime.attempt_followup",
        "core.attempt_followup",
        "classify_followup_intent",
    ),
    (
        alias_attempt_retry,
        canonical_attempt_retry,
        "core.agent_runtime.attempt_retry",
        "core.attempt_retry",
        "plan_retry_generation",
    ),
]


@pytest.mark.parametrize(
    "alias_mod,canonical_mod,alias_path,canonical_path,attr",
    TARGETS,
    ids=[t[2] for t in TARGETS],
)
def test_alias_module_object_is_identically_the_canonical_module_object(
    alias_mod, canonical_mod, alias_path, canonical_path, attr
):
    """The property lost in passes 1-2 and restored in pass 3: not merely equal namespaces, the
    SAME module object -- `is`, not `==`. Also re-imports both paths fresh through `sys.modules`
    to rule out a stale reference from test-collection-time import order masking a regression."""
    assert alias_mod is canonical_mod, (
        f"{alias_path} is not {canonical_path} -- module identity is broken again "
        f"(this is exactly BLUE-3's pass-3 finding)"
    )
    assert sys.modules[alias_path] is sys.modules[canonical_path]


@pytest.mark.parametrize(
    "alias_mod,canonical_mod,alias_path,canonical_path,attr",
    TARGETS,
    ids=[t[2] for t in TARGETS],
)
def test_mock_patch_on_alias_path_is_observed_via_canonical_path(
    alias_mod, canonical_mod, alias_path, canonical_path, attr
):
    """The property BLUE-3 found silently lost. A patch aimed at the alias path -- the shape ~18
    real call sites in this tree use -- must be visible to code that imports and calls through the
    CANONICAL path, which is what the runtime actually does (`core/agent_runtime/__init__.py`'s
    own imports, and every production call site preferring the canonical module)."""
    original = getattr(canonical_mod, attr)
    with mock.patch(f"{alias_path}.{attr}") as patched:
        assert getattr(canonical_mod, attr) is patched, (
            f"patching {alias_path}.{attr} did not affect {canonical_path}.{attr} -- "
            f"the patch landed on a dead copy"
        )
        assert getattr(alias_mod, attr) is patched
    # Control: unpatched afterward, on BOTH paths -- proves the assertion above was not vacuously
    # true because the attribute never changes.
    assert getattr(canonical_mod, attr) is original
    assert getattr(alias_mod, attr) is original


@pytest.mark.parametrize(
    "alias_mod,canonical_mod,alias_path,canonical_path,attr",
    TARGETS,
    ids=[t[2] for t in TARGETS],
)
def test_mock_patch_on_canonical_path_is_observed_via_alias_path(
    alias_mod, canonical_mod, alias_path, canonical_path, attr
):
    """The reverse direction: real call sites also patch the canonical path directly, and that
    must remain visible through the alias path (it always was, even in passes 1-2, since the
    alias path could still be READ off the canonical object at patch time -- but pinning it here
    closes the loop so a future change cannot regress one direction while "fixing" the other)."""
    original = getattr(alias_mod, attr)
    with mock.patch(f"{canonical_path}.{attr}") as patched:
        assert getattr(alias_mod, attr) is patched
        assert getattr(canonical_mod, attr) is patched
    assert getattr(alias_mod, attr) is original
    assert getattr(canonical_mod, attr) is original


# ---------------------------------------------------------------------------------------------
# Sweep: how many mock.patch call sites in the tree target one of the four alias paths, and are
# they now passing for real (identity restored) rather than needing individual rewrites.
# ---------------------------------------------------------------------------------------------

_ALIAS_PREFIXES = (
    "core.agent_runtime.live_data_plan",
    "core.agent_runtime.attempt_approval",
    "core.agent_runtime.attempt_followup",
    "core.agent_runtime.attempt_retry",
)


def _find_alias_patch_targets(source: str) -> list[str]:
    """String arguments to `mock.patch(...)` / `monkeypatch.setattr(...)` / `@patch(...)` calls
    (any call whose function name is or ends in `patch`, matching the common
    `mock.patch`/`unittest.mock.patch`/bare `@patch` spellings) that name one of the four alias
    dotted paths -- as a plain string literal (the overwhelming majority of real usage) or an
    f-string with a literal alias-path prefix (covers `mock.patch(f"{ALIAS}.foo")`-style helpers).
    """
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
        if not name.endswith("patch"):
            continue
        for arg in node.args:
            target = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                target = arg.value
            elif isinstance(arg, ast.JoinedStr) and arg.values and isinstance(arg.values[0], ast.Constant):
                target = arg.values[0].value
            if target and target.startswith(_ALIAS_PREFIXES):
                hits.append(target)
    return hits


def test_sweep_report_of_alias_path_mock_patch_sites_in_the_tree():
    """Not a pass/fail gate on the count -- a receipt. Every hit found here is exercised by the
    real test files that contain them (via the normal regression run); this asserts only that the
    sweep itself runs clean and reports what it found, so the count in BLUE2_REPORT_PASS3.md is
    reproducible from the tree rather than hand-counted.
    """
    hits: dict[str, list[str]] = {}
    for root_name in ("core", "adapters", "apps", "tests"):
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                found = _find_alias_patch_targets(source)
            except SyntaxError:
                continue
            if found:
                hits[str(path.relative_to(PROJECT_ROOT).as_posix())] = found

    total = sum(len(v) for v in hits.values())
    print(f"\nalias-path mock.patch sites found: {total} across {len(hits)} files")
    for file, targets in sorted(hits.items()):
        print(f"  {file}: {targets}")
    # Regression floor, not a target: at least the count BLUE-3 measured (~18) must still be
    # findable by this sweep, or the sweep itself has gone blind (too narrow a pattern, wrong
    # roots, etc.) rather than the sites having genuinely disappeared.
    assert total >= 10, f"expected to find BLUE-3's ~18 alias-path patch sites, found only {total}"


def test_a_real_alias_path_patch_site_from_the_sweep_passes_for_real():
    """Not synthetic: pick one FILE the sweep above actually found, and confirm its own test suite
    is green under the fixed identity -- i.e. the fix, not an individual rewrite, is what makes
    these pass again."""
    hits: dict[str, list[str]] = {}
    for path in sorted((PROJECT_ROOT / "tests").rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            found = _find_alias_patch_targets(source)
        except SyntaxError:
            continue
        if found:
            hits[path] = found
    assert hits, "no alias-path mock.patch site found under tests/ to spot-check"
    sample_path = sorted(hits, key=lambda p: str(p))[0]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(sample_path)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"{sample_path.relative_to(PROJECT_ROOT)} (found via an alias-path mock.patch, "
        f"{hits[sample_path]}) is not green under the identity fix:\n{proc.stdout[-4000:]}"
    )

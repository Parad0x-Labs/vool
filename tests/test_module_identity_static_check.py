"""Tests for ops/check_module_identity.py (R2, AUD-20260829-003).

Four things this pins:
1. The repo tree is CLEAN today (the four A9 shims are re-export shims, not swap shims).
2. The checker actually DETECTS the old swap idiom (positive control) -- otherwise "CLEAN"
   would be vacuous.
3. The checker does NOT flag the read-only `_THIS_MODULE = sys.modules[__name__]` idiom used by
   `core/brain_hive_dashboard.py` and `apps/vool_daemon.py` -- a false positive there would
   either break two unrelated, safe modules or train the next author to work around the checker
   instead of trusting it.
4. (Pass 3, BLUE-3's identity-vacuity finding.) The checker resolves the assigned key against the
   enclosing FILE's own derived module name, not just the literal token `__name__` -- so a module
   cannot reintroduce the original hazard by spelling its own name as a matching string literal
   or an f-string that reduces to it -- while the pass-3 fix itself
   (`core/agent_runtime/__init__.py` registering `sys.modules[f"{__name__}.<child>"]`, a PARENT
   assigning a CHILD name from single-threaded init) is correctly left unflagged.

Sabotage proof (I6) for this checker lives in the module docstring's worked example and was run
by hand against the real tree for BLUE2_REPORT.md / BLUE2_REPORT_PASS3.md; the unit tests below
are the synthetic, fast-running form of the same properties (detects the bad shapes, ignores the
safe ones).
"""

from __future__ import annotations

from ops import check_module_identity
from ops.check_module_identity import build_report, find_offending_lines

# The exact idiom the four A9 shims used to carry, byte-for-byte (see git history of
# core/agent_runtime/live_data_plan.py prior to the R2 fix).
_OLD_SWAP_IDIOM = """\
import sys

import core.live_data_plan as _canonical

sys.modules[__name__] = _canonical
"""

# The fixed re-export idiom now in all four shims.
_NEW_REEXPORT_IDIOM = """\
import core.live_data_plan as _canonical

for _name in dir(_canonical):
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = getattr(_canonical, _name)
del _name
"""

# The read-only self-reference idiom from core/brain_hive_dashboard.py / apps/vool_daemon.py.
_SAFE_SELF_REFERENCE_IDIOM = """\
import sys

_THIS_MODULE = sys.modules[__name__]


def uses_hooks():
    return _THIS_MODULE
"""


class TestFindOffendingLines:
    def test_detects_the_swap_idiom(self) -> None:
        assert find_offending_lines(_OLD_SWAP_IDIOM) == [5]

    def test_reexport_idiom_is_clean(self) -> None:
        assert find_offending_lines(_NEW_REEXPORT_IDIOM) == []

    def test_reading_sys_modules_dunder_name_is_not_flagged(self) -> None:
        # This is the precision check the task calls out explicitly: a checker that flags reads
        # as well as writes would break core/brain_hive_dashboard.py and apps/vool_daemon.py,
        # which both bind sys.modules[__name__] to a local name for a hooks/dependency-injection
        # self-reference and never touch the registry.
        assert find_offending_lines(_SAFE_SELF_REFERENCE_IDIOM) == []

    def test_detects_the_swap_idiom_even_via_tuple_unpacking(self) -> None:
        # The invariant is "no assignment to this subscript", not "no assignment via this one
        # statement shape" -- a tuple-unpacking or chained assignment must be caught too.
        source = "import sys\nsys.modules[__name__], other = _canonical, None\n"
        assert find_offending_lines(source) == [2]

    def test_unrelated_dotted_modules_attribute_is_not_flagged(self) -> None:
        # Only sys.modules[__name__] is the invariant's subject; a same-shaped subscript on an
        # unrelated object (or sys.modules keyed by something other than __name__) is not this
        # hazard and must not be flagged.
        source = "import sys\nsys.modules['core.other'] = _canonical\nregistry.modules[__name__] = x\n"
        assert find_offending_lines(source) == []


class TestSelfNameResolutionPass3:
    """BLUE-3's finding: the original check matched only the literal token `__name__`, which a
    module could evade by writing its OWN dotted name as a string literal instead -- functionally
    identical hazard, different spelling. These pin the fix without breaking the pass-3 remedy
    that fix had to keep legal (a parent package registering a CHILD name)."""

    def test_a_literal_string_matching_the_files_own_name_is_flagged(self) -> None:
        source = 'import sys\nsys.modules["core.agent_runtime.live_data_plan"] = _canonical\n'
        assert find_offending_lines(source, filename="core/agent_runtime/live_data_plan.py") == [2]

    def test_an_fstring_reducing_to_just_the_own_name_is_flagged(self) -> None:
        source = 'import sys\nsys.modules[f"{__name__}"] = _canonical\n'
        assert find_offending_lines(source, filename="core/agent_runtime/live_data_plan.py") == [2]

    def test_the_same_literal_string_in_an_unrelated_file_is_not_flagged(self) -> None:
        # The string must match THIS file's own derived name, not just look like a module path.
        source = 'import sys\nsys.modules["core.agent_runtime.live_data_plan"] = _canonical\n'
        assert find_offending_lines(source, filename="core/some_other_module.py") == []

    def test_parent_registers_child_via_fstring_is_not_flagged(self) -> None:
        # The pass-3 remedy itself: core/agent_runtime/__init__.py assigning a CHILD name.
        source = 'import sys\nsys.modules[f"{__name__}.live_data_plan"] = live_data_plan\n'
        assert find_offending_lines(source, filename="core/agent_runtime/__init__.py") == []

    def test_parent_registers_child_via_concatenation_is_not_flagged(self) -> None:
        source = 'import sys\nsys.modules[__name__ + ".attempt_retry"] = attempt_retry\n'
        assert find_offending_lines(source, filename="core/agent_runtime/__init__.py") == []

    def test_a_key_this_checker_cannot_statically_resolve_is_not_flagged(self) -> None:
        # Documented scope boundary, not a gap: a key built from something this checker cannot
        # evaluate (a name from elsewhere, a call, ...) is left alone rather than guessed at.
        source = "import sys\nsys.modules[some_computed_name] = _canonical\n"
        assert find_offending_lines(source, filename="core/agent_runtime/live_data_plan.py") == []

    def test_synthetic_filenames_without_a_derivable_module_name_still_catch_the_literal_token(
        self,
    ) -> None:
        # Backward compatibility: callers that never pass a real repo-relative filename (every
        # pre-pass-3 call site in this file) must keep working exactly as before -- the bare
        # `__name__` token is still caught even when no own-name can be derived.
        assert find_offending_lines(_OLD_SWAP_IDIOM) == [5]

    def test_synthetic_filenames_cannot_match_a_literal_string_since_no_own_name_exists(self) -> None:
        source = 'import sys\nsys.modules["whatever"] = _canonical\n'
        assert find_offending_lines(source) == []

    def test_the_real_pass_3_init_file_is_clean_and_actually_registers_all_four_aliases(self) -> None:
        """Not a synthetic snippet: the real `core/agent_runtime/__init__.py` on disk must both
        pass this checker AND actually contain the four registration lines -- guards against the
        checker being vacuously satisfied by a file that does nothing."""
        real_path = check_module_identity.PROJECT_ROOT / "core" / "agent_runtime" / "__init__.py"
        source = real_path.read_text(encoding="utf-8")
        assert find_offending_lines(source, filename="core/agent_runtime/__init__.py") == []
        for child in ("live_data_plan", "attempt_approval", "attempt_followup", "attempt_retry"):
            assert f'.{child}"] = {child}' in source, f"missing registration line for {child}"


class TestBuildReportAgainstTheRealTree:
    def test_repo_is_clean(self) -> None:
        report = build_report()
        assert report["status"] == "CLEAN", report["issues"]
        assert report["offending_modules"] == {}
        assert report["unparsable"] == []

    def test_the_four_fixed_shims_and_the_two_safe_self_reference_modules_are_all_scanned(self) -> None:
        # Precision has two failure directions: missing the files that matter, or flagging the
        # wrong ones. Assert all six are actually inside what got scanned (not skipped by an
        # overzealous ignore rule), on top of the CLEAN status above.
        report = build_report()
        scanned = {
            str(p.relative_to(check_module_identity.PROJECT_ROOT).as_posix())
            for p in check_module_identity._iter_python_files()
        }
        expected = {
            "core/agent_runtime/live_data_plan.py",
            "core/agent_runtime/attempt_approval.py",
            "core/agent_runtime/attempt_followup.py",
            "core/agent_runtime/attempt_retry.py",
            "core/brain_hive_dashboard.py",
            "apps/vool_daemon.py",
        }
        missing = expected - scanned
        assert not missing, f"expected files missing from the static-check scan: {missing}"
        for path in expected:
            assert path not in report["offending_modules"], f"unexpected flag on {path}"

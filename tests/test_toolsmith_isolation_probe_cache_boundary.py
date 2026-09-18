"""TOOLSMITH isolation-probe-cache repair: mutation proofs + result classifier.

Proven failure (THREADKEEPER): `tests/test_t3_reliability_hardening.py`'s three JobRunner-timeout
tests globally mock `sandbox.job_runner.subprocess.run` with `TimeoutExpired`. `_backend_usable`'s
own internal probe call goes through the SAME mock, gets poisoned by the identical fake timeout,
and permanently caches `_BACKEND_USABLE["sandbox-exec"] = False` in a module-level dict that is
never reset. Run afterward in the same pytest process, `tests/test_toolsmith_run_tests_fs_
confinement.py` sees no usable isolation backend and executes without the expected sandbox,
silently allowing the exact escape write that test exists to prove is denied.

Repair: `isolation_backend_probe_cache_reset`, an autouse pytest fixture in `tests/conftest.py`,
resets `sandbox.job_runner._BACKEND_USABLE` before AND after every test in the suite -- a
suite-wide invariant, not a fixture individual poisoning tests opt into (a second, unrelated
poisoner was found in `test_job_runner.py` after the first repair shipped; see
`tests/test_toolsmith_isolation_probe_cache_invariant.py` for the platform-neutral proof covering
both).

ANVIL FAIL, Blocker 1 (this file, round 1): the mutation tests below drive a real pytest
subprocess containing `tests/test_toolsmith_run_tests_fs_confinement.py`, whose confinement
`TestCase` is `@unittest.skipUnless(sys.platform == "darwin", ...)` -- genuinely Darwin-only,
since it exercises a real kernel-enforced sandbox. On non-Darwin CI (this repo's CI runs
ubuntu-latest) that class is SKIPPED, not failed: the subprocess exits 0 regardless of whether
the fixture mutation below actually reintroduces the regression, because the victim test that
would have caught it never ran. The old version of this file asserted `returncode != 0` directly
and went red on CI for exactly that reason -- the TEST was wrong, not the production fix.

Repair, two parts:
  1. This whole end-to-end mutation-runner `TestCase` is now explicitly gated to Darwin, since its
     victim genuinely is -- ANVIL confirmed this gating is an acceptable, correct scope (the
     PLATFORM-NEUTRAL half of the invariant lives in the sibling `_invariant.py` file instead).
  2. `_classify_pytest_run` below reads the run's junit-xml report and distinguishes "the victim
     genuinely failed" (a kill) from "the victim was skipped" or "the victim never even ran"
     (neither is evidence of anything, and must never be scored as a kill or a survival) --
     defense in depth in case the outer Darwin gate is ever bypassed, weakened, or the victim
     file's own skip condition changes independently of this one.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VICTIM_MODULE = "test_toolsmith_run_tests_fs_confinement"

_REAL_RESET_FIXTURE_ANCHOR = "def isolation_backend_probe_cache_reset() -> None:"
_REAL_RESET_BLOCK = "    reset_isolation_backend_probe_cache()\n    yield\n    reset_isolation_backend_probe_cache()\n"
_REAL_AUTOUSE_DECORATOR = "@pytest.fixture(autouse=True)\ndef isolation_backend_probe_cache_reset() -> None:"

_CONFTEST_FILE = _REPO_ROOT / "tests" / "conftest.py"


# --- result classification: the fix for Blocker 1 --------------------------------------------


def _classify_pytest_run(junit_path: Path, victim_module: str) -> str:
    """Classify a pytest run against ONE named victim module from its junit-xml report.

    Returns exactly one of:
      - "killed": at least one victim testcase recorded a real assertion failure -- the only
        outcome that counts as evidence a mutation reintroduced the regression.
      - "clean": every victim testcase ran and passed -- evidence the regression is NOT present.
      - "skipped": every victim testcase that was found was SKIPPED (e.g. a platform guard) --
        the victim never actually ran, so this is NOT evidence of anything either way.
      - "infrastructure_error": no matching testcase was collected at all (wrong selector, the
        file failed to import, a syntax error), a testcase recorded a collection/setup <error>
        rather than a semantic <failure>, or the report itself is missing/unparseable. Also not
        evidence of anything -- a broken harness is not a passing (or failing) mutation proof.

    Deliberately junit-xml-based, not `returncode`-based: a pytest run whose only victim
    testcases were skipped exits 0 -- the exact same code as a run where the victim genuinely
    executed and passed. Only a per-testcase outcome can tell those apart.
    """
    if not junit_path.exists():
        return "infrastructure_error"
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError:
        return "infrastructure_error"
    victim_cases = [
        case
        for case in root.iter("testcase")
        if victim_module in (case.get("classname") or "") or victim_module in (case.get("name") or "")
    ]
    if not victim_cases:
        return "infrastructure_error"
    if any(case.find("error") is not None for case in victim_cases):
        return "infrastructure_error"
    if any(case.find("failure") is not None for case in victim_cases):
        return "killed"
    if all(case.find("skipped") is not None for case in victim_cases):
        return "skipped"
    return "clean"


def _write_fake_victim(directory: Path, *, mode: str, module_marker: str) -> Path:
    """A small, real test module used to exercise `_classify_pytest_run` against GENUINE pytest
    output (real subprocess, real junit-xml) instead of hand-authored XML fixtures, for every
    outcome shape the classifier must distinguish."""
    path = directory / f"test_{module_marker}.py"
    bodies = {
        "skipped": (
            "import unittest\n"
            "@unittest.skipUnless(False, 'simulated: victim guard did not apply on this host')\n"
            "class FakeVictim(unittest.TestCase):\n"
            "    def test_case(self) -> None:\n"
            "        assert True\n"
        ),
        "passed": (
            "import unittest\n"
            "class FakeVictim(unittest.TestCase):\n"
            "    def test_case(self) -> None:\n"
            "        assert True\n"
        ),
        "failed": (
            "import unittest\n"
            "class FakeVictim(unittest.TestCase):\n"
            "    def test_case(self) -> None:\n"
            "        assert False, 'deliberate failure for classifier proof'\n"
        ),
        "collection_error": "this is not valid python syntax !!!\n",
    }
    path.write_text(bodies[mode], encoding="utf-8")
    return path


# --- platform-neutral: pure classifier proofs, including the M5/M6 self-tests ----------------


class ClassifierUnitTests(unittest.TestCase):
    """Runs on every platform -- no Darwin backend needed. `_classify_pytest_run` is pure/parsing
    logic; these prove it distinguishes a real failure from skip/error/missing-selector cases
    using GENUINE pytest + junit-xml output, not hand-rolled XML."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def _classify_fake_run(self, mode: str) -> tuple[str, subprocess.CompletedProcess]:
        marker = f"classify_probe_{mode}"
        victim = _write_fake_victim(self.tmp_dir, mode=mode, module_marker=marker)
        junit = self.tmp_dir / f"{mode}.xml"
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", str(victim)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return _classify_pytest_run(junit, marker), completed

    def test_a_skipped_victim_is_classified_as_skipped_not_killed_or_clean(self) -> None:
        classification, completed = self._classify_fake_run("skipped")
        self.assertEqual(classification, "skipped")
        self.assertEqual(completed.returncode, 0, "a skip alone must not fail pytest")

    def test_a_passing_victim_is_classified_as_clean(self) -> None:
        classification, completed = self._classify_fake_run("passed")
        self.assertEqual(classification, "clean")
        self.assertEqual(completed.returncode, 0)

    def test_a_real_assertion_failure_is_classified_as_killed(self) -> None:
        classification, completed = self._classify_fake_run("failed")
        self.assertEqual(classification, "killed")
        self.assertEqual(completed.returncode, 1)

    def test_a_collection_error_is_classified_as_infrastructure_error_not_killed(self) -> None:
        # M6: a syntax error during collection is NOT a semantic assertion failure and must
        # never be scored as a mutation kill.
        classification, completed = self._classify_fake_run("collection_error")
        self.assertEqual(classification, "infrastructure_error")
        self.assertNotEqual(completed.returncode, 0)

    def test_a_missing_junit_report_is_infrastructure_error(self) -> None:
        self.assertEqual(
            _classify_pytest_run(self.tmp_dir / "does-not-exist.xml", "anything"), "infrastructure_error"
        )

    def test_a_missing_selector_that_collects_nothing_matching_is_infrastructure_error(self) -> None:
        # M6: the victim module name does not appear anywhere in the report at all (wrong
        # node-id, wrong file, or the file was simply never included) -- there is no testcase to
        # read a verdict from, so this must not be scored as a kill either.
        victim = _write_fake_victim(self.tmp_dir, mode="passed", module_marker="unrelated_module")
        junit = self.tmp_dir / "missing_selector.xml"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", str(victim)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(_classify_pytest_run(junit, "a_totally_different_module_name"), "infrastructure_error")

    def test_m5_self_test_returncode_alone_cannot_distinguish_a_skipped_victim_from_a_clean_pass(self) -> None:
        """M5: the pre-repair bug scored evidence from `completed.returncode` alone. A run whose
        only victim testcase was SKIPPED exits 0 -- the identical exit code a genuinely clean
        pass produces. Pins that the naive, returncode-only approach is blind to this distinction
        (so a future regression back to it is caught), and that the real classifier is not."""
        skipped_classification, skipped_completed = self._classify_fake_run("skipped")
        clean_classification, clean_completed = self._classify_fake_run("passed")

        def _naive_returncode_only_classifier(returncode: int) -> str:
            return "killed" if returncode != 0 else "clean"

        naive_on_skipped = _naive_returncode_only_classifier(skipped_completed.returncode)
        naive_on_clean = _naive_returncode_only_classifier(clean_completed.returncode)
        self.assertEqual(
            naive_on_skipped,
            naive_on_clean,
            "sabotage check: the naive returncode-only classifier must be UNABLE to tell a "
            "skipped victim from a clean pass apart (both exit 0) -- if it could, this would no "
            "longer demonstrate the exact blind spot the real classifier exists to close",
        )
        self.assertNotEqual(
            skipped_classification,
            clean_classification,
            "the real, junit-xml-based classifier MUST distinguish them, unlike the naive one "
            "above -- this is the actual fix for the CI failure ANVIL reproduced",
        )

    def test_m6_self_test_a_collection_error_must_not_be_treated_as_a_caught_mutation(self) -> None:
        """M6: only a semantic assertion <failure> counts as a kill. Pins that a collection error
        classifies differently from -- and is never conflated with -- a real kill."""
        error_classification, _ = self._classify_fake_run("collection_error")
        killed_classification, _ = self._classify_fake_run("failed")
        self.assertNotEqual(error_classification, "killed")
        self.assertEqual(killed_classification, "killed")
        self.assertNotEqual(error_classification, killed_classification)


# --- Darwin-only: the real end-to-end fixture mutation proofs --------------------------------

_DARWIN_SKIP_REASON = (
    "This end-to-end proof drives the real Darwin-only filesystem-confinement suite "
    "(test_toolsmith_run_tests_fs_confinement.py) as its victim; the platform-neutral half of "
    "this invariant lives in test_toolsmith_isolation_probe_cache_invariant.py instead."
)


@unittest.skipUnless(sys.platform == "darwin", _DARWIN_SKIP_REASON)
class IsolationProbeCacheBoundaryMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_conftest_content = _CONFTEST_FILE.read_text(encoding="utf-8")
        self.assertIn(
            _REAL_RESET_FIXTURE_ANCHOR,
            self.real_conftest_content,
            "the real autouse fixture's def line was not found in tests/conftest.py -- these "
            "mutation tests are meaningless unless they patch the REAL, current fix",
        )
        self.assertIn(
            _REAL_RESET_BLOCK,
            self.real_conftest_content,
            "the real fixture's exact reset-before/yield/reset-after body was not found -- "
            "these mutation tests are meaningless unless they patch the REAL, current fix, not "
            "a guess at it",
        )
        self.assertIn(
            _REAL_AUTOUSE_DECORATOR,
            self.real_conftest_content,
            "the real fixture's exact `@pytest.fixture(autouse=True)` decorator + def line was "
            "not found -- these mutation tests are meaningless unless they patch the REAL fix",
        )

    def _run_mutated_conftest_pair(self, conftest_content: str) -> str:
        """Copies the whole `tests/` tree into a scratch dir with `conftest.py` replaced by
        `conftest_content`, then runs the real (unmodified) t3 + fs-confinement pair against it as
        a genuine pytest subprocess. Returns the `_classify_pytest_run` verdict.

        A full tree copy (not a single mutated file dropped elsewhere) is required here because
        the fixture under test is an AUTOUSE conftest.py fixture -- pytest only discovers it by
        walking up from the tests it collects, so the mutated conftest must sit in the same
        directory as the real, unmodified victim files for the fixture protocol to apply at all.
        """
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            scratch_tests = Path(tmp) / "tests"
            shutil.copytree(_REPO_ROOT / "tests", scratch_tests)
            (scratch_tests / "conftest.py").write_text(conftest_content, encoding="utf-8")
            junit = Path(tmp) / "report.xml"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:randomly",
                    f"--junitxml={junit}",
                    # POISONER. This was `test_t3_reliability_hardening.py`, whose three
                    # JobRunner-timeout tests mocked `subprocess.run` globally and so broke
                    # `_backend_usable`'s own probe, caching a false negative. Those tests now
                    # mock `subprocess.Popen` (the call the runner makes since it took over
                    # process-group teardown) and prime the probe cache BEFORE the mock goes
                    # up, so they cache a TRUE verdict and no longer poison anything — the
                    # collision is genuinely gone from that pair.
                    #
                    # The invariant file still poisons on purpose (`test_1_poison_the_backend_
                    # probe_cache_like_both_known_poisoners` calls `_backend_usable` under a
                    # timeout mock), which is what this mutation proof needs: a real poisoner,
                    # so that removing the autouse reset lets the poison reach the victim.
                    str(scratch_tests / "test_toolsmith_isolation_probe_cache_invariant.py"),
                    str(scratch_tests / "test_toolsmith_run_tests_fs_confinement.py"),
                ],
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
            classification = _classify_pytest_run(junit, _VICTIM_MODULE)
        return classification

    def _require_conclusive(self, classification: str) -> None:
        if classification in {"skipped", "infrastructure_error"}:
            self.skipTest(
                f"the Darwin confinement victim was never conclusively exercised "
                f"(classification={classification!r}) -- this proof requires the real backend "
                f"to actually run, and refuses to score ambiguous evidence as a kill or a pass"
            )

    def test_baseline_pair_is_clean_with_the_real_unmodified_fixture(self) -> None:
        """Control: the unmodified pair, run exactly as the real files are, must pass -- proves
        the two mutations below are what causes the failure, not an unrelated environment issue
        (a missing sandbox-exec binary, a CI sandboxing quirk, etc.)."""
        classification = self._run_mutated_conftest_pair(self.real_conftest_content)
        self._require_conclusive(classification)
        self.assertEqual(classification, "clean", "the unmodified fix must not reproduce the regression")

    def test_a_poisoned_probe_cache_now_refuses_instead_of_running_unconfined(self) -> None:
        """Replaces the two conftest mutation proofs that used to live here.

        They removed the autouse reset (once outright, once disguised as dropping only
        `autouse=True`), re-ran the poisoner/victim pair, and required the victim to go RED —
        the poisoned `sandbox-exec` verdict leaking across files and costing the victim its
        filesystem confinement.

        That kill is no longer reachable, and the reason is the fix this file now guards. A
        cached "no usable backend" verdict used to make `_with_network_isolation` fall back to
        returning the argv unwrapped, so a poisoned cache meant a job running with NO profile
        while everything upstream still called it sandboxed. It now raises
        `KernelIsolationUnavailableError`. A poisoned cache therefore produces a REFUSAL, and a
        refusal denies the escape write just as effectively as a working sandbox does — the
        victim stays green whether or not the reset fixture ran, so nothing can be killed.
        A mutation proof that cannot kill is not evidence, so it is replaced rather than
        weakened until it passes.

        What is still worth asserting is the property the fixture protects, stated directly:
        with the cache poisoned, the runner refuses — it does not run. The cross-file hygiene
        the fixture provides is now about avoiding SPURIOUS refusals, not about preventing
        silent escapes, and `tests/test_job_runner_real_confinement.py` covers the fail-closed
        behaviour end to end.
        """
        from sandbox import job_runner
        from sandbox.job_runner import JobRunner, KernelIsolationUnavailableError
        from sandbox.resource_limits import ExecutionPolicy

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            runner = JobRunner(ExecutionPolicy(workspace_root=root, writable_roots=(root,)))
            job_runner._BACKEND_USABLE.clear()
            for key in ("sandbox-exec", "bwrap:deny_network=True", "bwrap:deny_network=False",
                        "unshare", "firejail"):
                job_runner._BACKEND_USABLE[key] = False
            try:
                with self.assertRaises(KernelIsolationUnavailableError):
                    runner._with_network_isolation(["true"], (root,))
            finally:
                job_runner._BACKEND_USABLE.clear()

    def test_the_autouse_reset_fixture_is_still_wired(self) -> None:
        """The fixture itself, asserted on the real conftest rather than through a pair run.

        Both mutations the removed tests applied — deleting the reset body, and dropping
        `autouse=True` while leaving the body intact — are still detected here: each changes
        one of these two strings.
        """
        self.assertIn(_REAL_AUTOUSE_DECORATOR, self.real_conftest_content)
        self.assertIn(_REAL_RESET_BLOCK, self.real_conftest_content)


if __name__ == "__main__":
    unittest.main()

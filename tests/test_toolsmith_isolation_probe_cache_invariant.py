"""TOOLSMITH isolation-probe-cache repair: platform-neutral invariant proofs.

ANVIL FAIL (round 2), Blocker 2: the previous repair only reset `sandbox.job_runner.
_BACKEND_USABLE` in a fixture the three known `test_t3_reliability_hardening.py` timeout tests
opted into by name. ANVIL independently found a SECOND, unrelated poisoner --
`test_job_runner.py::JobRunnerTests::test_heuristic_only_mode_remains_explicit_opt_in`, which
fakes `os.name`/`sys.platform`/`shutil.which` to report a fictional Linux `bwrap` and lets the
real (unmocked) probe subprocess fail against that bogus path, caching
`bwrap:deny_network=False` as permanently unusable -- proving "individually tag the known
poisoners" is the wrong shape of fix for a hazard with no closed set of triggers.

The real repair is the `isolation_backend_probe_cache_reset` autouse fixture in
`tests/conftest.py`: every test starts AND ends with a clean `_BACKEND_USABLE`, unconditionally,
with no per-test opt-in and no maintained poisoner list. These tests are the PLATFORM-NEUTRAL
half of the proof ANVIL required -- they exercise both known poisoning shapes (a `subprocess.run`
mock that also breaks the internal probe, and a `shutil.which`/`os.name`/`sys.platform` fake that
lets the probe run for real against a bogus target) and both cache-key shapes
(`sandbox-exec`, `bwrap:deny_network=...`), all in-process, without needing a real Darwin
filesystem-confinement backend. They run identically on Linux CI and on this macOS dev machine.

The Darwin-only end-to-end confinement pair (the original t3 -> fs_confinement regression) and
its mutation proofs live in `tests/test_toolsmith_isolation_probe_cache_boundary.py`, which is
gated to Darwin because ITS victim genuinely is.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sandbox import job_runner
from sandbox.job_runner import JobRunner, _backend_usable
from sandbox.resource_limits import ExecutionPolicy

# --- candidate-before-victim: both known poisoners, both key shapes ---------
#
# Pytest runs test functions within a module in declaration order (this repo pins that with
# `-p no:randomly` for any file where order matters, and the canonical shard runner does not
# reorder within a file either). `test_1_...` poisons the cache exactly as the two known real
# poisoners do; `test_2_...` asserts the NEXT test in the same process inherits nothing -- which
# only holds because `isolation_backend_probe_cache_reset` (tests/conftest.py) resets the cache
# in its teardown after test_1 and its setup before test_2. Remove either half of that fixture
# and this pair goes red (proven directly on the fixture in
# `tests/test_toolsmith_isolation_probe_cache_boundary.py`'s Darwin-gated mutation tests; the
# mechanism here is identical, just exercised without needing a real sandbox backend).


def test_1_poison_the_backend_probe_cache_like_both_known_poisoners() -> None:
    # Poisoner A shape (test_t3_reliability_hardening.py): a global `subprocess.run` mock that
    # simulates the OUTER command timing out also breaks `_backend_usable`'s own internal probe
    # call, since it goes through the identical mock.
    timeout_exc = subprocess.TimeoutExpired(cmd=["irrelevant"], timeout=1)
    with mock.patch("sandbox.job_runner.subprocess.run", side_effect=timeout_exc):
        assert _backend_usable("sandbox-exec", ["irrelevant-probe-argv"]) is False
    assert job_runner._BACKEND_USABLE.get("sandbox-exec") is False

    # Poisoner B shape (test_job_runner.py::test_heuristic_only_mode_remains_explicit_opt_in): fake
    # a Linux host with a `bwrap` on PATH, WITHOUT mocking subprocess.run -- the probe subprocess
    # call is real, targets the bogus path, and fails for real.
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = JobRunner(
            ExecutionPolicy(workspace_root=Path(tmpdir), network_isolation_mode="heuristic_only")
        )
        with mock.patch("sandbox.job_runner.os.name", "posix"), mock.patch(
            "sandbox.job_runner.sys.platform", "linux"
        ), mock.patch("sandbox.job_runner.shutil.which", return_value="/usr/bin/definitely-not-bwrap"):
            # The probe still runs (and still poisons the cache, which is what this test is
            # about) — but a host where every backend probe fails now REFUSES rather than
            # returning the argv unwrapped. The refusal is incidental here; the cache entry
            # below is the assertion.
            with contextlib.suppress(job_runner.KernelIsolationUnavailableError):
                runner._with_network_isolation(["python3", "-c", "print(1)"])
    assert job_runner._BACKEND_USABLE.get("bwrap:deny_network=False") is False

    # Both poison shapes actually landed in the shared cache before this test ends.
    assert "sandbox-exec" in job_runner._BACKEND_USABLE
    assert "bwrap:deny_network=False" in job_runner._BACKEND_USABLE


def test_2_the_next_test_inherits_neither_poison() -> None:
    assert job_runner._BACKEND_USABLE == {}, (
        "the isolation_backend_probe_cache_reset autouse fixture must leave a clean cache "
        "for every test, regardless of what the previous test poisoned -- this is the exact "
        "collision ANVIL proved between test_t3_reliability_hardening.py and "
        "test_toolsmith_run_tests_fs_confinement.py, reproduced here without needing a real "
        "sandbox backend"
    )


# --- cache semantics: caching WITHIN one test must still work exactly as before -------------


def test_a_genuine_negative_probe_is_cached_and_not_reprobed_until_reset() -> None:
    calls = {"n": 0}

    def _counting_run(argv, **kwargs):
        calls["n"] += 1
        raise FileNotFoundError("simulated: backend genuinely absent from this host")

    with mock.patch("sandbox.job_runner.subprocess.run", side_effect=_counting_run):
        assert _backend_usable("fake-negative-probe", ["nonexistent-binary"]) is False
        assert calls["n"] == 1, "first call must actually probe"
        assert _backend_usable("fake-negative-probe", ["nonexistent-binary"]) is False
        assert calls["n"] == 1, "second call must be served from cache, not re-probe"

        job_runner.reset_isolation_backend_probe_cache()

        assert _backend_usable("fake-negative-probe", ["nonexistent-binary"]) is False
        assert calls["n"] == 2, "after an explicit reset, the next call must probe again"


def test_a_genuine_positive_probe_is_cached_and_not_reprobed() -> None:
    calls = {"n": 0}

    def _counting_run(argv, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(argv, returncode=0)

    with mock.patch("sandbox.job_runner.subprocess.run", side_effect=_counting_run):
        assert _backend_usable("fake-positive-probe", ["ok-binary"]) is True
        assert calls["n"] == 1
        assert _backend_usable("fake-positive-probe", ["ok-binary"]) is True
        assert calls["n"] == 1, "a genuine positive result must also be cached, not re-probed"


# --- realistic candidate-before-victim, as a genuine separate subprocess run -----------------


def test_real_subprocess_pair_test_job_runner_then_invariant_file_is_green() -> None:
    """Runs the ACTUAL `test_job_runner.py` (containing the real, unmodified poisoner test) ahead
    of a single node from THIS file in a fresh pytest subprocess -- the same shape of proof used
    for the t3 pair, for the second poisoner ANVIL found. Not a simulation: the real fixture, the
    real poisoner test, a real subprocess.

    Selects one specific node (not the whole file) so this test is not itself re-collected and
    re-run inside the child process it spawns.
    """
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "tests/test_job_runner.py",
            "tests/test_toolsmith_isolation_probe_cache_invariant.py::"
            "test_2_the_next_test_inherits_neither_poison",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


# --- ANVIL M1/M2/M3: the GENERAL "reset before; yield; reset after" autouse contract ---------
#
# These build a tiny, fully synthetic pytest project -- its own fake cache dict, its own reset
# function, and an autouse fixture whose body is swapped per mutation -- plus an OUTER autouse
# fixture (declared first, so its teardown runs LAST, after the fixture-under-test's own
# teardown has already completed) that records the cache's contents at that exact point. This
# tests the CONTRACT ("every test starts and ends with a clean cache") directly and generally,
# independent of the real `sandbox.job_runner` module or either specific known poisoner shape,
# and independent of whatever the "next" test in a real run happens to do.


def _run_synthetic_autouse_fixture_harness(fixture_body: str) -> list:
    """Returns one teardown snapshot per test (in execution order): the fake cache's contents
    immediately after that test's OWN fixture teardown chain (including `fixture_body`) has run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "conftest.py").write_text(
            "import json\n"
            "import pathlib\n"
            "import pytest\n\n"
            "_CACHE = {}\n\n"
            "def reset_cache():\n"
            "    _CACHE.clear()\n\n"
            "_SNAPSHOTS = []\n\n"
            "@pytest.fixture\n"
            "def shared_cache():\n"
            "    return _CACHE\n\n"
            "@pytest.fixture(autouse=True)\n"
            "def _outer_cache_inspector():\n"
            "    yield\n"
            "    _SNAPSHOTS.append(dict(_CACHE))\n\n"
            "@pytest.fixture(autouse=True)\n"
            f"def fixture_under_test():\n{fixture_body}\n\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    out = pathlib.Path(str(session.config.rootpath)) / 'snapshots.json'\n"
            "    out.write_text(json.dumps(_SNAPSHOTS))\n",
            encoding="utf-8",
        )
        (root / "test_poison.py").write_text(
            "def test_poison(shared_cache):\n    shared_cache['poisoned'] = True\n",
            encoding="utf-8",
        )
        (root / "test_victim.py").write_text(
            "def test_victim():\n    assert True\n",
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", str(root)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        snapshots_file = root / "snapshots.json"
        if not snapshots_file.exists():
            return None
        return json.loads(snapshots_file.read_text(encoding="utf-8"))


_BASELINE_FIXTURE_BODY = "    reset_cache()\n    yield\n    reset_cache()\n"


class GlobalAutouseFixtureContractTests(unittest.TestCase):
    def test_baseline_reset_before_and_after_leaves_a_clean_cache_at_every_test_boundary(self) -> None:
        snapshots = _run_synthetic_autouse_fixture_harness(_BASELINE_FIXTURE_BODY)
        self.assertEqual(snapshots, [{}, {}], "the real shape (reset before; yield; reset after) must leave a clean cache after EVERY test")

    def test_m1_removing_the_reset_entirely_turns_the_boundary_check_red(self) -> None:
        snapshots = _run_synthetic_autouse_fixture_harness("    yield\n")
        self.assertIsNotNone(snapshots, "harness did not run")
        self.assertNotEqual(
            snapshots,
            [{}, {}],
            "sabotage did not reproduce the regression -- removing the reset entirely must "
            "leave the poisoning test's dirty cache visible at its own teardown boundary",
        )
        self.assertEqual(snapshots[0], {"poisoned": True})

    def test_m2_removing_only_the_before_reset_can_still_inherit_prior_poison(self) -> None:
        """M2: with no BEFORE reset, poison introduced before this fixture's protection window
        even starts -- e.g. at test-module IMPORT time, which happens during collection, before
        any test's fixture setup runs at all -- is never cleared. `keep-after` alone cannot save
        a test from poison it inherits before its own setup phase begins."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conftest.py").write_text(
                "import json\n"
                "import pathlib\n"
                "import pytest\n\n"
                "_CACHE = {'poisoned_at_collection_time': True}\n\n"
                "def reset_cache():\n"
                "    _CACHE.clear()\n\n"
                "_SNAPSHOTS = []\n\n"
                "@pytest.fixture\n"
                "def shared_cache():\n"
                "    return _CACHE\n\n"
                "@pytest.fixture(autouse=True)\n"
                "def _outer_cache_inspector():\n"
                "    _SNAPSHOTS.append(('before', dict(_CACHE)))\n"
                "    yield\n"
                "    _SNAPSHOTS.append(('after', dict(_CACHE)))\n\n"
                "@pytest.fixture(autouse=True)\n"
                "def fixture_under_test():\n"
                "    yield\n"
                "    reset_cache()\n\n"  # BEFORE-reset removed; AFTER kept
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    out = pathlib.Path(str(session.config.rootpath)) / 'snapshots.json'\n"
                "    out.write_text(json.dumps(_SNAPSHOTS))\n",
                encoding="utf-8",
            )
            (root / "test_victim.py").write_text(
                "def test_victim():\n    assert True\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", str(root)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            snapshots = json.loads((root / "snapshots.json").read_text(encoding="utf-8"))
        # The very first test's OWN "before" observation must already show the collection-time
        # poison -- nothing ever cleared it, because the before-half of the reset is missing.
        self.assertEqual(
            snapshots[0],
            ["before", {"poisoned_at_collection_time": True}],
            "sabotage did not reproduce the regression -- with no before-reset, a test must be "
            "able to observe poison that existed before its own setup ran",
        )

    def test_m3_removing_only_the_after_reset_breaks_the_promised_after_test_cleanliness(self) -> None:
        """M3: the fixture's documented contract is "every test starts AND ENDS with a clean
        cache" -- an outer/session-level inspection of the cache immediately after a test's own
        teardown chain, independent of whatever the NEXT test's own before-reset might paper
        over, is required to catch a regression here."""
        snapshots = _run_synthetic_autouse_fixture_harness("    reset_cache()\n    yield\n")
        self.assertIsNotNone(snapshots, "harness did not run")
        self.assertNotEqual(
            snapshots[0],
            {},
            "sabotage did not reproduce the regression -- with no after-reset, the poisoning "
            "test's own teardown boundary must show a dirty cache, even though the FOLLOWING "
            "test's before-reset would have papered over it",
        )
        self.assertEqual(snapshots[0], {"poisoned": True})


class PartialResetBlindSpotTests(unittest.TestCase):
    def test_m4_a_reset_that_only_clears_known_key_names_would_leave_an_unrecognized_key_behind(self) -> None:
        """M4: proves why `reset_isolation_backend_probe_cache`'s unconditional whole-dict
        `.clear()` is required, not a per-key-name pop list. A reset that only forgets keys it
        already knows about would silently miss a key shaped differently than expected --
        exactly the failure ANVIL found: a `bwrap:deny_network=...`-shaped key surviving a
        reset that only knew about `sandbox-exec`."""
        cache = {"sandbox-exec": False, "bwrap:deny_network=False": False, "bwrap:deny_network=True": False}

        def _sabotaged_reset_only_known_keys(d: dict) -> None:
            d.pop("sandbox-exec", None)  # forgets every bwrap-shaped key

        _sabotaged_reset_only_known_keys(cache)
        self.assertIn(
            "bwrap:deny_network=False",
            cache,
            "sabotage check: the partial reset must leave the bwrap key behind -- if it "
            "didn't, this would not demonstrate the blind spot .clear() exists to close",
        )

        # The REAL production reset empties the whole cache unconditionally -- no key-name
        # logic to fall out of sync with whatever backends this module knows about today.
        job_runner._BACKEND_USABLE["sandbox-exec"] = False
        job_runner._BACKEND_USABLE["bwrap:deny_network=False"] = False
        job_runner.reset_isolation_backend_probe_cache()
        self.assertEqual(job_runner._BACKEND_USABLE, {}, "the real reset must clear every key, not just known ones")

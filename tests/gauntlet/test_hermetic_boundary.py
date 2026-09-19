"""The process boundary itself, under test.

This file runs in the PARENT and must never import the runtime -- that is the guarantee. It asserts
four different things, and the distinction matters:

* the child really is hermetic (measured paths, not environment variables);
* the parent really is unchanged afterwards (the contamination that started all this);
* a scenario group really shares ONE runtime (or cross-chat isolation means nothing);
* the protocol really refuses to let an infrastructure fault become a product verdict.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.gauntlet.hermetic import groups as G  # noqa: N812  (reads as the module name at every call site)
from tests.gauntlet.hermetic import protocol as P  # noqa: N812  (reads as the module name at every call site)
from tests.gauntlet.hermetic.runner import PROJECT_ROOT, child_env, run_group

# The two files whose failure proved the gauntlet was polluting the parent interpreter.
CONTAMINATION_TARGETS = (
    "tests/test_public_hive_bridge.py",
    "tests/test_must_keep_is_a_guarantee_not_a_sort_key.py",
)


def _real_config_home() -> Path:
    """Resolved WITHOUT importing the runtime: `active_vool_home()/config`, VOOL_HOME honoured.

    Duplicated rather than imported on purpose -- importing `core.runtime_paths` into the parent
    would start the very thing this file exists to keep out.
    """
    override = str(os.environ.get("VOOL_HOME") or "").strip()
    base = Path(override).expanduser() if override else PROJECT_ROOT / ".vool_local"
    return (base / "config").resolve()


# ------------------------------------------------------------------- 1. the child is hermetic


def test_the_child_uses_its_own_config_home_and_never_the_real_one() -> None:
    """Filesystem evidence, not an environment assertion.

    `core/web/api/runtime.py:590` writes `agent-bootstrap.json` to
    `active_config_home_dir()`, which is `active_vool_home()/config`, which honours `VOOL_HOME`.
    So the check is: the child REPORTS a config home under its own temp root, and the developer's
    real `agent-bootstrap.json` is byte-for-byte untouched across the run.
    """
    real = _real_config_home() / "agent-bootstrap.json"
    before = (real.read_bytes(), real.stat().st_mtime) if real.exists() else None

    outcome = run_group("mutation_probe")
    payload = outcome.require_result()

    child_home = Path(payload["self_test"]["config_home"])
    assert child_home != _real_config_home(), "child used the developer's real config home"
    assert not str(child_home).startswith(str(PROJECT_ROOT)), child_home

    after = (real.read_bytes(), real.stat().st_mtime) if real.exists() else None
    assert after == before, (
        "the child modified the real agent-bootstrap.json -- config-home isolation is not working"
    )


def test_the_child_reports_the_real_routing_stack_ran() -> None:
    """Hermetic must not mean hollow: ranking, capability truth and Autopilot all really ran."""
    payload = run_group("mutation_probe").require_result()
    facts = payload["self_test"]
    assert facts["adapter_class"] == "_Scripted"
    assert facts["ranked"], facts
    assert facts["autopilot_lane"] not in ("", "human"), facts
    assert facts["selftest_status"] == 200
    assert facts["selftest_weather"] == ["kaunas"]


# --------------------------------------------------------- 2. the parent survives child runs


def _parent_state() -> dict[str, object]:
    """Representative parent state. Deliberately excludes anything runtime-owned."""
    return {
        "env": dict(os.environ),
        "modules_core": sorted(m for m in sys.modules if m.startswith(("core.", "apps.", "adapters."))),
        "threads": sorted(t.name for t in __import__("threading").enumerate()),
    }


def test_running_children_does_not_change_parent_process_state() -> None:
    before = _parent_state()
    for group in ("mutation_probe", "cross_chat"):
        run_group(group).require_result()
    after = _parent_state()
    assert after["env"] == before["env"], "child runs mutated the parent environment"
    assert after["modules_core"] == before["modules_core"], (
        "the parent imported runtime modules while driving children -- the boundary leaks"
    )
    assert after["threads"] == before["threads"], "child runs left threads in the parent"


def test_driving_children_never_imports_the_bootstrap_module_into_the_parent() -> None:
    """`core.web.api.runtime` is where `bootstrap_runtime_services` lives, and the gauntlet must not
    be what pulls it into the parent.

    Stated as a DELTA rather than an absolute, and both halves of that matter.

    Absolute forms of this assertion are wrong twice over. The repository's own root conftest does
    `from apps.vool_agent import VoolAgent` (tests/conftest.py:21) for EVERY pytest run here --
    measured with one trivial test and no gauntlet present. And in a canonical SHARD this file sits
    beside suites that legitimately import `core.web.api.runtime` themselves (test_runtime_bootstrap,
    test_web0_service_integration, ...), so "nothing in this session ever imported it" fails for
    reasons the boundary neither causes nor may fix.

    What the boundary actually promises is narrower and checkable: running a child must not be what
    drags the bootstrap module in. Measured across the call, so a neighbour's import cannot mask a
    regression and cannot manufacture one either.
    """
    before = {m for m in sys.modules if m.startswith("core.web.api.runtime")}
    run_group("mutation_probe").require_result()
    after = {m for m in sys.modules if m.startswith("core.web.api.runtime")}
    assert after == before, f"driving a child imported the bootstrap module: {sorted(after - before)}"


@pytest.mark.parametrize("iteration", [0, 1])
def test_pollution_matrix_targets_stay_green_around_children(iteration: int) -> None:
    """A -> B -> C, in one real pytest process, repeated.

    The child gauntlet runs inside a pytest session that ALSO runs the two files whose failure was
    the original contamination proof. If the boundary leaks, those files fail exactly as they did
    before -- which is what makes this an end-to-end proof rather than a state diff.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *CONTAMINATION_TARGETS,
         "tests/gauntlet/test_hermetic_boundary.py::test_the_child_reports_the_real_routing_stack_ran",
         *CONTAMINATION_TARGETS, "-q", "-p", "no:randomly", "-p", "no:cacheprovider", "--tb=short"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        f"iteration {iteration}: targets failed in a session that also ran the hermetic gauntlet\n"
        f"{result.stdout[-4000:]}"
    )


# ------------------------------------------------------- 3. a group is ONE runtime, not many


def test_cross_chat_runs_entirely_inside_one_child_runtime() -> None:
    evidence = run_group("cross_chat").require_result()["evidence"]
    assert evidence["runtime_bootstraps"] == 1, evidence["runtime_bootstraps"]
    pids = {evidence["pid"]}
    assert len(pids) == 1
    assert evidence["alpha_session"] != evidence["beta_session"]


def test_beta_never_receives_alpha_history_and_alpha_keeps_its_own() -> None:
    """The product claim the boundary exists to make sayable."""
    evidence = run_group("cross_chat").require_result()["evidence"]
    alpha, beta = evidence["alpha_marker"], evidence["beta_marker"]
    by_label = {turn["label"]: turn for turn in evidence["turns"]}

    for label in ("B1", "B2"):
        seen = by_label[label]["prompt_seen_by_model"] + by_label[label]["reply"]
        assert alpha not in seen, f"{label} received chat A's marker\n{by_label[label]}"
    for label in ("A1", "A2", "A3"):
        seen = by_label[label]["prompt_seen_by_model"] + by_label[label]["reply"]
        assert beta not in seen, f"{label} received chat B's marker\n{by_label[label]}"

    # ...and the isolation is not simply "no history at all".
    assert alpha in by_label["A2"]["prompt_seen_by_model"], (
        "chat A lost its OWN history, so the isolation assertions above are vacuous"
    )


def test_the_group_registry_cannot_drift_to_one_child_per_test() -> None:
    assert len(G.PRODUCT_GROUPS) <= G.MAX_GROUPS, (
        f"{len(G.PRODUCT_GROUPS)} groups exceeds the cap of {G.MAX_GROUPS}; the unit of isolation "
        "is the SCENARIO GROUP, not the test"
    )
    # A group must be able to hold several turns/chats -- the structural opposite of per-test children.
    evidence = run_group("cross_chat").require_result()["evidence"]
    assert len(evidence["turns"]) >= 5, evidence


# --------------------------------------------------- 4. the protocol refuses false verdicts


def test_an_unknown_group_is_an_infrastructure_fault_not_a_product_verdict() -> None:
    outcome = run_group("no_such_group")
    assert outcome.kind == P.KIND_INFRA
    with pytest.raises(AssertionError, match="GAUNTLET INFRASTRUCTURE FAULT"):
        outcome.require_result()


def test_a_hanging_child_is_killed_and_reported_as_infrastructure() -> None:
    started = time.monotonic()
    outcome = run_group("_hang_forever", timeout_s=15.0)
    elapsed = time.monotonic() - started
    assert outcome.kind == P.KIND_INFRA, outcome
    assert "timeout" in outcome.infra_reason.lower(), outcome.infra_reason
    assert elapsed < 90, f"timeout did not fire promptly ({elapsed:.0f}s)"
    with pytest.raises(AssertionError, match="GAUNTLET INFRASTRUCTURE FAULT"):
        outcome.require_result()


def test_infrastructure_faults_keep_the_child_stderr_for_debugging() -> None:
    outcome = run_group("no_such_group")
    try:
        outcome.require_result()
    except AssertionError as exc:
        assert "child stderr" in str(exc)
        assert "child rc" in str(exc)
    else:  # pragma: no cover
        pytest.fail("an infra_error must not pass require_result()")


# ------------------------------------------- 5. mutations must cross the process boundary


def test_a_parent_monkeypatch_cannot_be_claimed_as_a_child_mutation(monkeypatch) -> None:
    """M6. A parent-side patch does not exist in the child, so it can never be a mutation kill.

    This is the honesty check on the future mutation strategy: if a parent monkeypatch DID change
    the child's evidence, every mutation result from here on would be suspect.
    """
    import core.runtime_evidence as parent_module
    from core.runtime_evidence import _ACTIVITY_WINDOW

    monkeypatch.setattr(parent_module, "_ACTIVITY_WINDOW", 999999, raising=True)
    assert parent_module._ACTIVITY_WINDOW == 999999

    evidence = run_group("mutation_probe").require_result()["evidence"]
    assert evidence["activity_window"] != 999999, (
        "a PARENT monkeypatch reached the CHILD -- process isolation is broken and every future "
        "mutation claim would be unsound"
    )


def test_an_on_disk_production_mutation_does_reach_the_child() -> None:
    """The mechanism that WILL be used for real mutations: edit source, launch child, restore.

    A benign, reversible edit to a real production constant. The child is a fresh interpreter, so it
    imports the mutated file from disk and reports the mutated value. The file is restored
    byte-identically afterwards and that is asserted, not assumed.
    """
    from tests import _source_guard as source_guard

    # `finally` restores this on every failure Python can see, and on none of the failures that
    # actually strand it: this test spawns a child, and a supervisor reaping the run on a timeout
    # leaves `core/runtime_evidence.py` holding `_ACTIVITY_WINDOW = 4242` with nothing to say so.
    # `mutated_source` journals the original to disk BEFORE writing the mutation, so a killed run
    # is repairable afterwards by `recover_orphaned()` rather than merely regrettable.
    relative = "core/runtime_evidence.py"
    target = PROJECT_ROOT / relative
    original = target.read_bytes()
    baseline = run_group("mutation_probe").require_result()["evidence"]["activity_window"]
    mutated = original.replace(b"_ACTIVITY_WINDOW = 200", b"_ACTIVITY_WINDOW = 4242")
    assert mutated != original, "mutation anchor not found in core/runtime_evidence.py"
    with source_guard.mutated_source(relative, mutated):
        observed = run_group("mutation_probe").require_result()["evidence"]["activity_window"]
    assert target.read_bytes() == original, "production source was not restored byte-identically"
    assert not source_guard._journal_path(relative).exists(), "a clean exit must clear its journal"
    assert baseline == 200, baseline
    assert observed == 4242, (
        f"the child did not observe the on-disk production mutation (saw {observed}); "
        "cross-process mutation is the only honest mutation strategy for this gate"
    )


def test_child_env_strips_the_developers_shell_state() -> None:
    """Determinism: whatever the invoking shell exported must not decide what the child sees."""
    env = child_env(Path("/tmp/gauntlet-fake-home"), port=49999)
    assert env["VOOL_HOME"] == "/tmp/gauntlet-fake-home"
    assert env["VOOL_DISABLE_MESH_DAEMON"] == "1"
    assert env["VOOL_DAEMON_BIND_PORT"] == "49999"

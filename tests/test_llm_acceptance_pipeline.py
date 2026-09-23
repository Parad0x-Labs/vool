from __future__ import annotations

import time
from pathlib import Path

import yaml

from core.llm_eval.pack import parse_pytest_summary, run_pytest_pack

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_workflow() -> dict:
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "llm_acceptance.yml").read_text(encoding="utf-8"))


def _install_step_run(job: dict) -> str:
    step = next(s for s in job["steps"] if s.get("name") == "Install dependencies")
    return str(step["run"])


def test_pytest_summary_counts_the_singular_error_row() -> None:
    """The archived weekly run (35600263193) ended every failing scenario with pytest's
    singular row -- "10 warnings, 1 error in 0.75s" -- while the recorded summary read
    errors: 0. Status stayed red through the exit code, but the numbers lied; the
    singular form must count."""
    summary = parse_pytest_summary("10 warnings, 1 error in 0.75s")
    assert summary["errors"] == 1


def test_pytest_summary_keeps_counting_plural_rows() -> None:
    summary = parse_pytest_summary("2 failed, 3 passed, 1 skipped, 4 deselected in 1.2s")
    assert summary["passed"] == 3
    assert summary["failed"] == 2
    assert summary["skipped"] == 1
    assert summary["deselected"] == 4


def test_a_setup_import_error_keeps_the_pack_red(tmp_path: Path) -> None:
    """The original weekly-lane failure class: a module missing from the runner's
    environment fails every matching scenario at SETUP (ModuleNotFoundError through the
    global liquefy conftest fixture). Such a pack must report red with the error
    counted, never green and never a crash."""
    target = tmp_path / "test_missing_import_setup.py"
    target.write_text(
        "import vool_acceptance_probe_module_that_does_not_exist_20260923\n"
        "def test_never_reaches_the_body() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    pack = run_pytest_pack(name="setup_error_probe", repo_root=tmp_path, targets=[str(target)])
    assert pack["status"] == "fail"
    assert pack["exit_code"] != 0
    assert pack["summary"]["errors"] == 1
    assert "ModuleNotFoundError" in pack["stdout"]


def test_a_collection_error_keeps_the_pack_red(tmp_path: Path) -> None:
    pack = run_pytest_pack(name="collection_error_probe", repo_root=tmp_path, targets=[str(tmp_path / "no_such_file.py")])
    assert pack["status"] == "fail"
    assert pack["exit_code"] != 0


def test_an_execution_bound_reports_a_hung_pack_red_without_raising(tmp_path: Path) -> None:
    """A pack that exceeds its execution bound must come back as a RED result carrying
    the timeout evidence -- not as an unhandled TimeoutExpired that leaves no report
    and not as a silent hang that eats the job budget."""
    target = tmp_path / "test_hangs_forever.py"
    target.write_text(
        "import time\n"
        "def test_hangs() -> None:\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    started = time.perf_counter()
    pack = run_pytest_pack(name="hang_probe", repo_root=tmp_path, targets=[str(target)], timeout_seconds=3)
    elapsed = time.perf_counter() - started
    assert elapsed < 30, "the bound must actually terminate the pack"
    assert pack["timed_out"] is True
    assert pack["status"] == "fail"
    assert pack["exit_code"] == 124
    assert "exceeded the 3s execution bound" in pack["stderr"]


def test_the_weekly_fast_lane_is_provisioned_like_the_ci_shards_that_run_the_same_files() -> None:
    """Root cause of the failed weekly run: the fast lane installs only .[dev] while
    its selection (scenario targets plus every LLM-keyword tests/ file changed in the
    last 48h) is a subset of the CI suite, which provisions .[dev,browser,companion,evm].
    The global liquefy conftest fixture imports zstandard (declared under [companion]),
    so the thinner install failed every scenario at setup. This pins the extras parity,
    the checkout depth the 48h inventory needs, and the explicit job bound."""
    workflow = _load_workflow()
    fast = workflow["jobs"]["llm-acceptance-fast"]
    assert fast["runs-on"] == "ubuntu-latest"
    assert isinstance(fast.get("timeout-minutes"), int), "the fast job needs an explicit execution bound"

    install = _install_step_run(fast)
    assert '-e ".[dev,browser,companion,evm]"' in install, (
        "the fast lane must install the same extras as the CI shards that run these files green"
    )

    checkout = next(s for s in fast["steps"] if "checkout" in str(s.get("uses") or ""))
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "the 48h regression inventory walks git log; a shallow clone truncates it to the tip commit"
    )

    runs = "\n".join(str(s.get("run") or "") for s in fast["steps"])
    assert "--skip-live-runtime" in runs, "the weekly fast gate must not touch live providers"
    assert "--pack-timeout" in runs, "each pytest pack must be bounded so a hang reports red instead of eating the job"
    # The platform tools the inventory-eligible lanes need on Ubuntu 24.04, same as CI.
    assert "install -y bubblewrap" in runs
    assert "playwright install --with-deps chromium" in runs


def test_the_live_lane_stays_opt_in_and_bounded() -> None:
    workflow = _load_workflow()
    live = workflow["jobs"]["llm-acceptance-live"]
    assert live["if"] == "github.event_name == 'workflow_dispatch' && inputs.run_live_runtime", (
        "live runtime acceptance must remain an explicitly dispatched, opt-in lane"
    )
    assert live["needs"] == "llm-acceptance-fast"
    assert isinstance(live.get("timeout-minutes"), int), "the live job needs an explicit execution bound"
    assert '-e ".[dev,browser,companion,evm]"' in _install_step_run(live), (
        "the live lane reruns every fast section and needs the same extras"
    )

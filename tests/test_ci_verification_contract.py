from __future__ import annotations

import copy
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by compatibility CI on Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The authoritative gate is the shard matrix the migration-era CI adopted (owner commits
# 9fd7ee7/006d874 + PR #11's hidden-file artifact law): the verify job lints with the PINNED
# ruff and produces the canonical pytest collection, every collected file runs in exactly one
# of the ten Linux shards (duration-aware through ops/shard_resolver.py over the committed
# evidence snapshot) or the routed macOS job, and every job's logs
# upload unconditionally. The single-job `ops/verify.py` gate this suite pinned before the
# migration no longer exists in the workflow; the weakening law it enforced — exact commands,
# no disabled steps, no swallowed output, exact dependency pins — is restated here against the
# current structure.
#: The authoritative pytest argv every shard forwards through the timing wrapper: collection
#: flags only -- selection comes from the resolver's file list appended after these tokens.
#: (Before the timing wrapper this was `python -m pytest` + these args; the wrapper runs
#: pytest.main in-process with the SAME argv, so the args themselves are unchanged law.)
EXPECTED_SHARD_PYTEST_PREFIX = (
    "-q",
    "--tb=short",
    "-p",
    "no:cacheprovider",
)
#: The measurement wrapper every Linux shard runs its pytest through (PR #35): it forwards the
#: EXACT authoritative argv after its own `--` separator, its exit status IS pytest's, and it
#: writes the per-file timing manifest into .verification-logs/ beside the shard's file list.
#: Pinned token for token like the pytest prefix itself: the wrapper is a measurement seam, not
#: a place to hide command changes -- behind it, the invocation must still forward exactly
#: EXPECTED_SHARD_PYTEST_PREFIX and still consume exactly the resolver's file list. The
#: --node-budgets tokens are the duration-aware phase bound: a file with a measured duration in
#: the committed snapshot may spend up to measured*1.5 in one phase before the flat 600s bound
#: applies (a module-scoped fixture legitimately holds a whole file budget inside one opaque
#: setup; measured: run 36063857499 shard 0 killed a 2647.95s-measured file at 600s). The
#: loader fails soft when the snapshot is absent, so this pin holds on trees without it.
EXPECTED_SHARD_WATCHDOG_PREFIX = (
    "python", "ops/pytest_watchdog.py", "--phase-timeout", "600",
    "--node-budgets", "ops/shard_weights.json",
    "--output", ".verification-logs/shard-${{ matrix.shard }}-watchdog", "--",
)
EXPECTED_SHARD_TIMING_PREFIX = (
    "python",
    "ops/pytest_timing.py",
    "--output",
    ".verification-logs/shard-${{ matrix.shard }}-timing.json",
    "--",
)
#: The virtual-display wrapper the Linux shards run their pytest under: the wallet-handoff
#: contract opens one genuinely headful Chromium session (a handoff needs a VISIBLE browser), and
#: these runners have no display, so the headful lane cannot open without a framebuffer. Pinned
#: token for token: the wrapper is a display server, not a place to hide command changes -- the
#: invocation behind it must still pass through the pinned timing wrapper and the exact
#: authoritative pytest argv, and still consume exactly the resolver's file list for its shard.
EXPECTED_SHARD_DISPLAY_PREFIX = (
    "xvfb-run",
    "-a",
    "--server-args=-screen 0 1280x1024x24",
)
EXPECTED_LINT_COMMAND = "python -m ruff check ."
EXPECTED_COLLECTION_COMMAND = (
    "python ops/pytest_manifest.py --repo-root . --output .verification-logs/pytest-manifest.json -- -q"
)
SHARD_FILE_LIST_REF = "shard-${{ matrix.shard }}-files.txt"
EXPECTED_SHARDS = [str(index) for index in range(10)]


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _assert_upload_step(job: dict[str, Any], name: str) -> None:
    upload = _named_step(job, name)
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == ".verification-logs/"
    # PR #11 law: upload-artifact v7 silently drops dot-directories — without this flag the
    # .verification-logs/ uploads were EMPTY all along and every shard diagnosis ran blind.
    assert upload["with"].get("include-hidden-files") is True


def _assert_authoritative_contract(workflow: dict[str, Any]) -> None:
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    for event in ("push", "pull_request"):
        assert event in triggers
        assert "paths-ignore" not in triggers[event]

    verify = workflow["jobs"]["verify"]
    assert verify["if"] == "github.event_name != 'workflow_dispatch' || !inputs.focused_ci"
    setup_steps = [
        step for step in verify["steps"] if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    assert len(setup_steps) == 1
    assert setup_steps[0]["with"]["python-version"] == "3.12.13"

    lint = _named_step(verify, "Lint (pinned ruff)")
    assert "if" not in lint
    assert lint["run"].strip() == EXPECTED_LINT_COMMAND
    collection = _named_step(verify, "Canonical pytest collection")
    assert "if" not in collection
    assert collection["run"].strip() == EXPECTED_COLLECTION_COMMAND

    tests = workflow["jobs"]["tests"]
    assert tests["needs"] == "verify" or tests["needs"] == ["verify"]
    assert "if" not in tests
    matrix_shards = tests["strategy"]["matrix"]["shard"]
    assert matrix_shards == EXPECTED_SHARDS, "every shard must stay in the matrix: none may be dropped"

    resolver = _named_step(tests, "Resolve this shard's test files")
    assert "if" not in resolver
    run_step = _named_step(tests, "Run shard ${{ matrix.shard }}")
    assert "if" not in run_step
    command_text = str(run_step["run"])
    assert "--collect-only" not in command_text, "a shard run weakened to collection-only executes nothing"
    tokens = shlex.split(command_text)
    if tuple(tokens[: len(EXPECTED_SHARD_DISPLAY_PREFIX)]) == EXPECTED_SHARD_DISPLAY_PREFIX:
        tokens = tokens[len(EXPECTED_SHARD_DISPLAY_PREFIX) :]
    assert tuple(tokens[: len(EXPECTED_SHARD_WATCHDOG_PREFIX)]) == EXPECTED_SHARD_WATCHDOG_PREFIX
    tokens = tokens[len(EXPECTED_SHARD_WATCHDOG_PREFIX) :]
    assert tuple(tokens[: len(EXPECTED_SHARD_TIMING_PREFIX)]) == EXPECTED_SHARD_TIMING_PREFIX, (
        "the shard must run through the timing wrapper with its manifest in .verification-logs/"
    )
    tokens = tokens[len(EXPECTED_SHARD_TIMING_PREFIX) :]
    assert tuple(tokens[: len(EXPECTED_SHARD_PYTEST_PREFIX)]) == EXPECTED_SHARD_PYTEST_PREFIX
    assert command_text.rstrip().endswith(
        f"$(tr '\\n' ' ' < .verification-logs/{SHARD_FILE_LIST_REF})"
    ), "the run must consume exactly the resolver's file list for its own shard"
    validate_forwarded = tuple(
        token for token in tokens if token.startswith("--tb=")
    )
    from ops.pytest_shards import validate_pytest_args

    validate_pytest_args(validate_forwarded)
    _assert_upload_step(tests, "Upload shard log")

    macos = workflow["jobs"]["macos"]
    assert macos["needs"] == "verify" or macos["needs"] == ["verify"]
    assert "if" not in macos
    macos_run = _named_step(macos, "Run the macOS-only suites")
    assert "if" not in macos_run
    _assert_upload_step(macos, "Upload macos job log")

    assert workflow["jobs"]["build"]["needs"] == ["verify", "tests", "macos"]

    for job in workflow["jobs"].values():
        assert not job.get("continue-on-error", False)
        for step in job.get("steps", []):
            assert not step.get("continue-on-error", False)
            command_text = str(step.get("run", ""))
            assert "|| true" not in command_text
            assert "| tail" not in command_text
            assert "| tee" not in command_text


def test_push_and_pr_ci_use_the_exact_authoritative_gate() -> None:
    _assert_authoritative_contract(_load_workflow())


@pytest.mark.parametrize(
    "mutation",
    ("floating_python", "collect_only", "step_disabled", "job_disabled", "watchdog_removed", "watchdog_unbounded"),
)
def test_contract_mutations_are_rejected(mutation: str) -> None:
    workflow = copy.deepcopy(_load_workflow())
    verify = workflow["jobs"]["verify"]
    if mutation == "floating_python":
        setup = next(step for step in verify["steps"] if "setup-python" in step.get("uses", ""))
        setup["with"]["python-version"] = "3.12"
    elif mutation == "collect_only":
        gate = _named_step(workflow["jobs"]["tests"], "Run shard ${{ matrix.shard }}")
        gate["run"] += " --collect-only"
    elif mutation == "step_disabled":
        _named_step(workflow["jobs"]["tests"], "Run shard ${{ matrix.shard }}")["if"] = False
    elif mutation == "watchdog_removed":
        gate = _named_step(workflow["jobs"]["tests"], "Run shard ${{ matrix.shard }}")
        gate["run"] = gate["run"].replace("ops/pytest_watchdog.py", "ops/not_a_watchdog.py")
    elif mutation == "watchdog_unbounded":
        gate = _named_step(workflow["jobs"]["tests"], "Run shard ${{ matrix.shard }}")
        gate["run"] = gate["run"].replace("--phase-timeout 600", "--phase-timeout 0")
    else:
        verify["if"] = False

    with pytest.raises((AssertionError, ValueError)):
        _assert_authoritative_contract(workflow)


def test_verification_dependencies_are_exactly_pinned() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = set(project["project"]["optional-dependencies"]["dev"])

    # Exactly pinned, whatever the version currently is: a floor like ">=9.1" lets a new pytest
    # change collection semantics the shard manifest was built against. The version itself lives
    # only in pyproject — the gate (ops/verify.py) and the install-surface contract read it from
    # there, so a dependabot bump lands in one place. Copies of the literal here broke exactly
    # that on every past bump.
    for tool in ("pytest", "ruff"):
        exact = {item for item in dev if re.fullmatch(rf"{tool}==[0-9][0-9A-Za-z.\-]*", item)}
        assert exact, f"{tool} must carry an exact == pin in the dev extra, not a floor"
        assert len(exact) == 1, f"{tool} is declared more than once in the dev extra"


def test_manual_diagnostics_cannot_impersonate_full_gate_checks():
    workflow = _load_workflow()
    diagnostic = workflow["jobs"]["focused_ci_diagnostics"]
    assert diagnostic["if"] == "github.event_name == 'workflow_dispatch' && inputs.focused_ci"
    assert diagnostic["name"] == "focused-ci-diagnostics-not-merge-gate"
    assert diagnostic["timeout-minutes"] == 12
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["workflow_dispatch"]["inputs"]["focused_ci"]["default"] is False
    assert triggers["workflow_dispatch"]["inputs"]["focused_ci"]["type"] == "boolean"
    for job in ("verify", "tests", "macos", "build"):
        name = workflow["jobs"][job]["name"]
        assert "github.event_name == 'workflow_dispatch' && inputs.focused_ci" in name
        assert f"diagnostic-unused-{job}" in name
    assert "'diagnostic' || 'full'" in workflow["concurrency"]["group"]


CI_YML = WORKFLOW_PATH
LLM_YML = REPO_ROOT / ".github" / "workflows" / "llm_acceptance.yml"

# ---------------------------------------------------------------------------
# Browser preflight contracts (folded here from a standalone file: every new
# collected test file re-partitions the ten shards through ops/shard_resolver.py,
# and the documented TestTemporaryRules order-dependence (served-fixture-lane
# owner) makes a partition shift a gate roulette until that owner lands the fix).
# These contracts belong to this file's subject anyway: the ci.yml preflight step.
# ---
# The browser preflight must prove a POSITIVE number of successful render probes.
#
# The provisioning step in ci.yml (the tests matrix job) and its verbatim copy in llm_acceptance.yml
# discover the playwright cache's chromium binaries and probe each ``chrome-headless-shell``
# entry with a bounded sandboxed launch plus a rendered-output check. The loop's ``continue``
# skips every other binary — historically including the whole list when only the full Chrome
# build exists — and ``probe_fail`` started at 0, so a cache with nothing to probe passed the
# preflight without a single render. These contracts pin the repaired control flow by
# executing the ACTUAL script extracted from the workflow YAML under a stubbed environment:
# every demanded shape (no binaries, full-Chrome-only, headless-shell-only, launch failure,
# timeout, bad output, real success, mixed cache) is driven through the real text, and the
# two workflow copies may not drift apart.
# """
#
# The provisioning step in ci.yml (the tests matrix job) and its verbatim copy
# in llm_acceptance.yml are pinned by executing the ACTUAL step script extracted
# from the workflow YAML under a stubbed environment.
# ---------------------------------------------------------------------------

STEP_NAME = "Provision the served-browser lane (Playwright chromium)"

PROBE_MARK = "probe_fail=0"
PROBE_END = "exit $probe_fail"

GOOD_DOM = "<title>vool-probe</title>vool-probe"


def _steps(path: Path, job: str):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"][job]["steps"]


def _jobs_with_probing_step(path: Path) -> list[str]:
    import yaml

    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    return [job for job, body in jobs.items()
            if any(s.get("name") == STEP_NAME and PROBE_MARK in str(s.get("run", "")) for s in body.get("steps", []))]


_CI_PROBE_JOBS = _jobs_with_probing_step(CI_YML)
assert len(_CI_PROBE_JOBS) == 1, f"ci.yml: the probing step must live in exactly one job, found {_CI_PROBE_JOBS}"
_CI_PROBE_JOB = _CI_PROBE_JOBS[0]


def _preflight_script(path: Path, job: str) -> str:
    """The full run-script of the one step that contains the render probe."""
    matches = [s for s in _steps(path, job)
               if s.get("name") == STEP_NAME and PROBE_MARK in str(s.get("run", ""))]
    assert len(matches) == 1, f"{path.name}/{job}: expected exactly one probing '{STEP_NAME}' step"
    return str(matches[0]["run"])


def _probe_section(script: str) -> str:
    start = script.index(PROBE_MARK)
    end = script.index(PROBE_END) + len(PROBE_END)
    return script[start:end]


def _bin_stub(behavior: str, log_path: Path) -> str:
    bodies = {
        "good": f"printf '%s\\n' '{GOOD_DOM}'",
        "bad-output": 'printf \'%s\\n\' "a dom without the probe marker"',
        "fail": "echo 'stub chromium launch failure' >&2; exit 3",
        "hang": "sleep 60",
    }
    return f"#!/bin/sh\necho \"$0\" >> '{log_path}'\n{bodies[behavior]}\nexit 0\n"


def _write_stubs(sandbox: Path, layout: str, behavior: str, apparmor_rc: int = 0) -> Path:
    """HOME + PATH stubs reproducing one provisioning shape. Returns the stub-bin dir."""
    home = sandbox / "home"
    cache = home / ".cache" / "ms-playwright"
    bin_log = sandbox / "bins.log"
    entries = {"empty": [], "full": ["chrome"], "shell": ["chrome-headless-shell"],
               "mixed": ["chrome", "chrome-headless-shell", "chrome-headless-shell"]}
    for index, name in enumerate(entries[layout]):
        binary = cache / f"build-{index}" / "chrome-linux" / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text(_bin_stub(behavior, bin_log), encoding="utf-8")
        binary.chmod(0o755)

    stubbin = sandbox / "stubbin"
    captured = sandbox / "captured"
    stubbin.mkdir(parents=True, exist_ok=True)
    captured.mkdir(parents=True, exist_ok=True)
    sudo_log = sandbox / "sudo.log"

    (stubbin / "python").write_text(
        '#!/bin/sh\n'
        '# test double: `python -m playwright install` — the cache above IS the install\n'
        'if [ "$2" = "playwright" ]; then exit 0; fi\n'
        'echo "stub python: unexpected invocation: $*" >&2\nexit 1\n',
        encoding="utf-8",
    )
    (stubbin / "sudo").write_text(
        "#!/bin/sh\n"
        f'for arg in "$@"; do echo "$arg" >> "{sudo_log}"; done\n'
        'case "$1" in\n'
        '  tee)\n'
        f'    shift; cat > "{captured}/$(basename "$1")"; exit 0 ;;\n'
        '  apparmor_parser)\n'
        f'    echo "stub apparmor_parser diagnostic" >&2; exit {apparmor_rc} ;;\n'
        '  apt-get|sysctl)\n'
        '    exit 0 ;;\n'
        'esac\n'
        'exit 0\n',
        encoding="utf-8",
    )
    # Portable test double for GNU timeout: drops the script's generous "-k 5s 60s" bound
    # (3 leading args) and enforces the test's own watchdog, exiting 124 on a kill so the
    # probe's render check fails exactly as under the real timeout.
    (stubbin / "timeout").write_text(
        "#!/bin/sh\n"
        "shift 3\n"
        'bound="${VOOL_STUB_TIMEOUT_S:-3}"\n'
        '"$@" &\n'
        "pid=$!\n"
        '( sleep "$bound"; kill -9 "$pid" 2>/dev/null ) &\n'
        "watchdog=$!\n"
        'wait "$pid"; rc=$?\n'
        'kill "$watchdog" 2>/dev/null; wait "$watchdog" 2>/dev/null\n'
        'if [ "$rc" -ge 128 ]; then exit 124; fi\n'
        'exit "$rc"\n',
        encoding="utf-8",
    )
    for stub in stubbin.iterdir():
        stub.chmod(0o755)
    return stubbin


def _run_preflight(script: str, sandbox: Path, stubbin: Path):
    for name in ("vool-chromium-probe.dom", "vool-chromium-probe.err"):
        Path(f"/tmp/{name}").unlink(missing_ok=True)
    (sandbox / "tmp").mkdir(exist_ok=True)
    env = {
        "PATH": f"{stubbin}:/bin:/usr/bin",
        "HOME": str(sandbox / "home"),
        "TMPDIR": str(sandbox / "tmp"),
        "VOOL_STUB_TIMEOUT_S": "3",
    }
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _probe_invocations(sandbox: Path) -> int:
    log = sandbox / "bins.log"
    return log.read_text(encoding="utf-8").count("chrome-headless-shell") if log.exists() else 0


@pytest.mark.parametrize("layout", ["empty", "full", "shell", "mixed"])
def test_preflight_shapes(layout, tmp_path):
    """No binaries, full-Chrome-only: refused. Headless-shell/mixed (good render): pass."""
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    stubbin = _write_stubs(sandbox, layout, "good")
    result = _run_preflight(_preflight_script(CI_YML, _CI_PROBE_JOB), sandbox, stubbin)
    out = result.stdout + result.stderr

    if layout == "empty":
        assert result.returncode != 0
        assert "no chromium binaries" in out
    elif layout == "full":
        # The historical hole: only full Chrome exists, the probe loop skipped everything
        # and probe_fail stayed 0. The preflight must refuse to pass having probed nothing.
        assert result.returncode != 0
        assert "no chrome-headless-shell binary" in out
        assert "probed nothing" in out
        assert _probe_invocations(sandbox) == 0
    else:
        assert result.returncode == 0, out
        assert _probe_invocations(sandbox) == (1 if layout == "shell" else 2)
        profile = (sandbox / "captured" / "vool-playwright-chromium").read_text(encoding="utf-8")
        assert profile.count("userns,") == (1 if layout == "shell" else 3)
    sudo_log = sandbox / "sudo.log"
    if sudo_log.exists():  # the empty-cache shape exits before any sudo call
        assert "sysctl" not in sudo_log.read_text(encoding="utf-8")  # narrow profile, never global relaxation


@pytest.mark.parametrize("behavior", ["fail", "bad-output", "hang"])
def test_failed_launch_timeout_and_bad_output_all_fail(behavior, tmp_path):
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    stubbin = _write_stubs(sandbox, "shell", behavior)
    result = _run_preflight(_preflight_script(CI_YML, _CI_PROBE_JOB), sandbox, stubbin)
    out = result.stdout + result.stderr
    assert result.returncode != 0
    assert "cannot render" in out
    assert _probe_invocations(sandbox) == 1  # the probe RAN and its failure was counted
    if behavior == "fail":
        assert "stub chromium launch failure" in result.stderr


def test_probe_sections_of_both_workflows_stay_verbatim_identical():
    ci_probe = _probe_section(_preflight_script(CI_YML, _CI_PROBE_JOB))
    for job in _jobs_with_probing_step(LLM_YML):
        llm_probe = _probe_section(_preflight_script(LLM_YML, job))
        assert ci_probe == llm_probe, (
            "llm_acceptance.yml documents its preflight as verbatim from ci.yml — the copies must not drift"
        )


def test_probe_requires_a_positive_count_in_the_shipped_text():
    """Static pin of the repaired control flow, independent of the stub harness."""
    scripts = [_preflight_script(CI_YML, _CI_PROBE_JOB)]
    scripts += [_preflight_script(LLM_YML, job) for job in _jobs_with_probing_step(LLM_YML)]
    assert len(scripts) == 2
    for script in scripts:
        assert re.search(r"probe_count=\$\(\(probe_count \+ 1\)\)", script)
        assert re.search(r'\[ "\$probe_count" -eq 0 \]', script)


# ---------------------------------------------------------------------------
# AppArmor fail-fast contracts (folded here for the same partition-stability
# reason as the preflight contracts above; subject is the same file's
# provisioning scripts).
# ---------------------------------------------------------------------------

BWRAP_STEP = "Provision the kernel sandbox backend (bubblewrap)"
CHROMIUM_RELAX_MARK = "refusing to relax the host-wide unprivileged-userns restriction"


def _bwrap_scripts() -> dict[str, str]:
    import yaml

    found: dict[str, str] = {}
    for path in (CI_YML, LLM_YML):
        jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
        for job, body in jobs.items():
            for step in body.get("steps", []):
                if step.get("name") == BWRAP_STEP:
                    key = f"{path.name}:{job}"
                    assert key not in found
                    found[key] = str(step["run"])
    return found


def _run_with_bwrap_stub(sandbox: Path, stubbin: Path, script: str):
    (stubbin / "bwrap").write_text(
        '#!/bin/sh\necho "$0 $*" >> "' + str(sandbox / "bwrap.log") + '"\nexit 0\n',
        encoding="utf-8",
    )
    (stubbin / "bwrap").chmod(0o755)
    return _run_preflight(script, sandbox, stubbin)


def test_every_linux_lane_keeps_its_bwrap_script_verbatim_identical():
    scripts = _bwrap_scripts()
    assert len(scripts) >= 3, "ci.yml tests + focused diagnostics + llm_acceptance copies"
    distinct = {text.rstrip() for text in scripts.values()}
    assert len(distinct) == 1, f"bwrap provisioning copies drifted: {list(scripts)}"


def test_no_workflow_relaxes_the_host_wide_userns_policy():
    for path in (CI_YML, LLM_YML):
        assert "apparmor_restrict_unprivileged_userns=0" not in path.read_text(encoding="utf-8"), (
            f"{path.name}: a global unprivileged-userns relaxation crept back in"
        )


@pytest.mark.parametrize("apparmor_rc", [0, 1])
def test_bwrap_provisioning_accepted_and_refused_shapes(apparmor_rc, tmp_path):
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    stubbin = _write_stubs(sandbox, "empty", "good", apparmor_rc=apparmor_rc)
    script = next(iter(_bwrap_scripts().values()))
    result = _run_with_bwrap_stub(sandbox, stubbin, script)
    out = result.stdout + result.stderr
    sudo_log = (sandbox / "sudo.log").read_text(encoding="utf-8")

    if apparmor_rc == 0:
        assert result.returncode == 0, out
        assert (sandbox / "captured" / "bwrap").read_text(encoding="utf-8").count("userns,") == 1
        assert (sandbox / "bwrap.log").exists(), "the confined-launch probe must still run under the accepted profile"
    else:
        assert result.returncode != 0
        assert CHROMIUM_RELAX_MARK in out
        assert "::error::" in out
        assert "stub apparmor_parser diagnostic" in out, "the parser's own refusal reason must be shown"
        assert (sandbox / "bwrap.log").exists() is False, "a refused profile must not proceed to launch"
    assert "sysctl" not in sudo_log


@pytest.mark.parametrize("apparmor_rc", [0, 1])
def test_chromium_provisioning_accepted_and_refused_shapes(apparmor_rc, tmp_path):
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    stubbin = _write_stubs(sandbox, "shell", "good", apparmor_rc=apparmor_rc)
    script = _preflight_script(CI_YML, _CI_PROBE_JOB)
    result = _run_with_bwrap_stub(sandbox, stubbin, script)
    out = result.stdout + result.stderr
    sudo_log = (sandbox / "sudo.log").read_text(encoding="utf-8")
    bins_log = sandbox / "bins.log"

    if apparmor_rc == 0:
        assert result.returncode == 0, out
        assert bins_log.exists() and "chrome-headless-shell" in bins_log.read_text(encoding="utf-8")
    else:
        assert result.returncode != 0
        assert "::error::" in out and CHROMIUM_RELAX_MARK in out
        assert "stub apparmor_parser diagnostic" in out
        assert not bins_log.exists(), "a refused profile must fail before any render probe"
    assert "sysctl" not in sudo_log

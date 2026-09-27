"""The browser preflight must prove a POSITIVE number of successful render probes.

The provisioning step in ci.yml (the tests matrix job) and its verbatim copy in llm_acceptance.yml
discover the playwright cache's chromium binaries and probe each ``chrome-headless-shell``
entry with a bounded sandboxed launch plus a rendered-output check. The loop's ``continue``
skips every other binary — historically including the whole list when only the full Chrome
build exists — and ``probe_fail`` started at 0, so a cache with nothing to probe passed the
preflight without a single render. These contracts pin the repaired control flow by
executing the ACTUAL script extracted from the workflow YAML under a stubbed environment:
every demanded shape (no binaries, full-Chrome-only, headless-shell-only, launch failure,
timeout, bad output, real success, mixed cache) is driven through the real text, and the
two workflow copies may not drift apart.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LLM_YML = REPO_ROOT / ".github" / "workflows" / "llm_acceptance.yml"
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


def _write_stubs(sandbox: Path, layout: str, behavior: str) -> Path:
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
        '  apparmor_parser|apt-get|sysctl)\n'
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

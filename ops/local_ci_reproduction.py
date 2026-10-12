"""One-command, source-pinned local reproduction of the CI tests-matrix shard leg.

The problem this tool exists to end: local regression evidence was produced by
ad-hoc ``pytest`` invocations whose selection, order, environment and exits
never matched the CI shard leg, so a local green (or red) said nothing about
what automatic CI would do. This tool reproduces the ci.yml ``tests`` matrix
step faithfully and records enough identity to pin every reproduction:

  * SELECTION: the ci.yml collection step verbatim (``pytest --collect-only
    -q -p no:cacheprovider``, ``::``-line parse, sorted unique files, the 13
    macOS-only files routed out), then ``ops/shard_resolver.py`` for the
    shard partition. An explicit ordered file list (historical evidence) can
    be pinned instead -- the two are DIFFERENT selections and are never
    conflated: every run names its selection source, label and sha256.
  * ENVIRONMENT: python/OS/package fingerprints, isolated per-run ``VOOL_HOME``
    and pytest ``--basetemp``, no ``PYTEST_ADDOPTS`` inheritance.
  * EXECUTION: the exact CI command shape ``ops/pytest_watchdog.py -- ...
    ops/pytest_timing.py -- -q --tb=short -p no:cacheprovider <files>``, with
    raw stdout/stderr sinks, real exit propagation, completion vs
    interruption vs stall, and per-node outcomes plus first error text.
  * HONESTY: missing files, missing scripts, non-POSIX platforms and broken
    identity pins are refused BEFORE any execution is claimed. Facility gaps
    (Xvfb, Linux-only sandboxing, uninstalled optional packages) are recorded
    as facts -- never used to exonerate a failure, never silently dropped.

What a local run can never be is also recorded: the CI Linux/Xvfb leg is
UNRUN locally unless ``xvfb-run`` and a Linux runtime are actually present,
and the historical CI job's head is GitHub's synthetic PR merge commit, not
this repository's branch head (see RESULT ``head_identity``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as pyplatform
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

#: Schema of the RESULT.json every run writes; consumers refuse anything else.
SCHEMA = "vool.local-ci-reproduction.v1"

#: The 13 genuinely macOS-only files ci.yml routes to the macos job. Pinned
#: together with .github/workflows/ci.yml by tests/test_delivery_contracts.py;
#: a drift here is a defect in this tool, not a routing decision.
MACOS_ONLY_FILES = frozenset({
    "tests/test_macos_app_packaging.py",
    "tests/updater/test_e2e_sandbox.py",
    "tests/updater/test_boot_api_ui.py",
    "tests/updater/test_final_p1.py",
    "tests/updater/test_cli_and_tools.py",
    "tests/test_artifact_readers_contracts.py",
    "tests/test_artifact_readers_served_m1.py",
    "tests/test_artifact_readers_served_m2.py",
    "tests/test_audio_dictation.py",
    "tests/test_media_product_served.py",
    "tests/wallet/test_wallet_approval_methods.py",
    "tests/wallet/test_wallet_device_auth_ipc.py",
    "tests/wallet/test_wallet_export_ui_served.py",
})

#: CI installs ``.[dev,browser,companion,evm]``; these modules decide whether
#: the corresponding lanes can even be collected locally. Probed, recorded,
#: never exonerated: a missing module shows up as a collection failure in the
#: run itself and stays a failure.
PACKAGE_PROBES: tuple[tuple[str, str], ...] = (
    ("pytest", "pytest"), ("fastapi", "fastapi"), ("httpx", "httpx"),
    ("yaml", "pyyaml"), ("psutil", "psutil"), ("pydantic", "pydantic"),
    ("uvicorn", "uvicorn"), ("starlette", "starlette"),
    ("playwright", "playwright"), ("pypdf", "pypdf"), ("nacl", "pynacl"),
    ("eth_account", "eth-account"), ("eth_abi", "eth-abi"),
    ("eth_utils", "eth-utils"), ("solders", "solders"),
    ("keyring", "keyring"), ("requests", "requests"),
)

#: Exit codes this tool owns. Everything else is the wrapped command's real
#: exit, propagated unchanged; a refusal never claims execution.
EXIT_REFUSED_PREFLIGHT = 90
EXIT_REFUSED_PIN = 91
EXIT_HARVEST_INCONSISTENT = 92

_OPS_SCRIPTS = (
    "ops/pytest_watchdog.py", "ops/pytest_timing.py", "ops/pytest_execution.py",
    "ops/shard_resolver.py", "ops/shard_plan.py",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_pytest_env(env: dict[str, str]) -> dict[str, str]:
    """The collection/manifest tools clear these; the shard leg must too, or a
    developer shell silently changes selection or plugin loading between runs."""

    for variable in ("PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_PLUGINS"):
        env.pop(variable, None)
    return env


def _run_capture(command: list[str], *, cwd: Path, env: dict[str, str],
                 stdout_path: Path, stderr_path: Path, timeout: float | None = None
                 ) -> subprocess.CompletedProcess:
    with stdout_path.open("wb") as out_handle, stderr_path.open("wb") as err_handle:
        process = subprocess.run(
            command, cwd=str(cwd), env=env, stdout=out_handle, stderr=err_handle,
            timeout=timeout,
        )
    return process


def git_info(repo: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    status_short = git("status", "--porcelain")
    return {
        "head": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "branch": git("branch", "--show-current") or "(detached)",
        "dirty": bool(status_short),
        "status_short": status_short.splitlines()[:50],
        "remotes": git("remote", "-v").splitlines(),
    }


def environment_fingerprint(python: str) -> dict[str, Any]:
    probe = subprocess.run(
        [python, "-c",
         "import sys; print(sys.version); print(sys.executable); print(sys.platform)"],
        capture_output=True, text=True, timeout=60,
    )
    version_line, executable, sys_platform = ([*probe.stdout.splitlines(), "", "", ""])[:3]
    packages: dict[str, str] = {}
    for module, distribution in PACKAGE_PROBES:
        check = subprocess.run(
            [python, "-c",
             f"import {module}; print(getattr({module}, '__version__', ''))"],
            capture_output=True, text=True, timeout=60,
        )
        packages[distribution] = (
            f"{check.stdout.strip() or 'unknown-version'}" if check.returncode == 0
            else "MISSING"
        )
    os_detail: dict[str, str] = {}
    sw_vers = shutil.which("sw_vers")
    if sw_vers:
        for line in subprocess.run([sw_vers], capture_output=True, text=True,
                                   timeout=30).stdout.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                os_detail[key.strip()] = value.strip()
    return {
        "python_version": version_line,
        "python_executable": executable,
        "sys_platform": sys_platform,
        "platform_python": pyplatform.platform(),
        "platform_machine": pyplatform.machine(),
        "os_detail": os_detail,
        "packages": packages,
        "utc_now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _xvfb_status() -> dict[str, Any]:
    available = shutil.which("xvfb-run") is not None
    return {
        "xvfb_run": "available" if available else "MISSING",
        "automatic_ci_linux_xvfb_leg": (
            "RUN-LOCALLY-IF-SELECTED" if available else
            "UNRUN locally: no xvfb-run and no Linux runtime; local evidence is "
            "macOS-only and the automatic CI Linux/Xvfb leg remains UNRUN"
        ),
    }


# --------------------------------------------------------------------------- #
# Collection (ci.yml "Resolve this shard's test files", verbatim semantics)
# --------------------------------------------------------------------------- #

def _isolated_collection_env(profile_root: Path) -> dict[str, str]:
    """Cleaned env plus a fresh VOOL_HOME: even collection imports product code,
    and an isolation leak at collect time is still an isolation leak."""

    env = _clean_pytest_env(dict(os.environ))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    home = profile_root / f"collect-home-{stamp}-{os.getpid()}"
    home.mkdir(parents=True, exist_ok=True)
    env["VOOL_HOME"] = str(home)
    return env


def canonical_collection(repo: Path, outdir: Path, python: str,
                         profile_root: Path | None = None) -> dict[str, Any]:
    """Replicate the ci.yml collection step: collect-only, ``::``-line parse,
    sorted unique files, macOS routing with the drift RECORDED, not repaired.

    On a machine without every CI optional dependency some files fail import
    during collection and drop out of the parse. CI machines install the full
    extras set; a local machine that does not WILL drift, and that drift is a
    fact about the local environment -- written to ``macos_not_collected`` /
    noted in the counts, never silently patched up. Pinning a historical list
    (``--selection-explicit``) is the explicit, hashed act of reproducing a
    selection that was collected on a complete environment."""

    outdir.mkdir(parents=True, exist_ok=True)
    env = (_isolated_collection_env(profile_root) if profile_root is not None
           else _clean_pytest_env(dict(os.environ)))
    process = _run_capture(
        [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=repo, env=env,
        stdout_path=outdir / "collect-stdout.txt",
        stderr_path=outdir / "collect-stderr.txt",
    )
    stdout = (outdir / "collect-stdout.txt").read_text(encoding="utf-8", errors="replace")
    files = sorted({line.split("::", 1)[0] for line in stdout.splitlines() if "::" in line})
    macos_collected = sorted(set(files) & MACOS_ONLY_FILES)
    macos_not_collected = sorted(MACOS_ONLY_FILES - set(files))
    linux_files = [f for f in files if f not in MACOS_ONLY_FILES]
    (outdir / "linux-collected.txt").write_text("\n".join(linux_files) + "\n", encoding="utf-8")
    return {
        "collection_command_exit": process.returncode,
        "collected_files_total": len(files),
        "linux_files": len(linux_files),
        "macos_routed_out": macos_collected,
        "macos_only_not_collected_locally": macos_not_collected,
        "linux_collected_sha256": sha256_file(outdir / "linux-collected.txt"),
    }


def canonical_manifest(repo: Path, outdir: Path, python: str,
                        profile_root: Path | None = None) -> dict[str, Any]:
    """The verify leg's ``ops/pytest_manifest.py`` invocation, unchanged; its
    per-item manifest is the canonical pairing key for any shard evidence."""

    outdir.mkdir(parents=True, exist_ok=True)
    manifest = outdir / "pytest-manifest.json"
    env = (_isolated_collection_env(profile_root) if profile_root is not None
           else _clean_pytest_env(dict(os.environ)))
    process = _run_capture(
        [python, str(Path("ops") / "pytest_manifest.py"), "--repo-root", ".",
         "--output", str(manifest), "--", "-q"],
        cwd=repo, env=env,
        stdout_path=outdir / "manifest-stdout.txt",
        stderr_path=outdir / "manifest-stderr.txt",
    )
    payload: dict[str, Any] = {}
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    return {
        "manifest_command_exit": process.returncode,
        "item_count": payload.get("item_count"),
        "manifest_sha256": sha256_file(manifest) if manifest.exists() else None,
    }


def resolve_canonical_shard(repo: Path, outdir: Path, python: str, *, shard: int,
                            shards: int) -> dict[str, Any]:
    """Compute the CI shard partition with ``--platform linux`` so the plan is
    the one automatic CI computed (the committed weights snapshot is Linux
    data; on any other sys.platform the resolver would correctly refuse it and
    silently fall back to a DIFFERENT partition -- exactly the conflation this
    tool exists to prevent). Pure data computation; nothing is executed."""

    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / f"shard-{shard}-files.txt"
    env = _clean_pytest_env(dict(os.environ))
    process = _run_capture(
        [python, str(Path("ops") / "shard_resolver.py"),
         "--shard", str(shard), "--shards", str(shards),
         "--files", str(outdir / "linux-collected.txt"),
         "--weights", str(Path("ops") / "shard_weights.json"),
         "--platform", "linux",
         "--output", str(output)],
        cwd=repo, env=env,
        stdout_path=outdir / "resolver-stdout.txt",
        stderr_path=outdir / "resolver-stderr.txt",
    )
    files = [line for line in output.read_text(encoding="utf-8").splitlines()
             if line.strip()] if output.exists() else []
    return {
        "resolver_command_exit": process.returncode,
        "shard": shard, "shards": shards,
        "resolver_platform_override": "linux (CI partition; committed snapshot is Linux data)",
        "files": files,
        "mode_line": (outdir / "resolver-stdout.txt").read_text(
            encoding="utf-8", errors="replace").strip().splitlines()[-1] if files else "",
    }


# --------------------------------------------------------------------------- #
# Selection identity
# --------------------------------------------------------------------------- #

def build_selection(*, source: str, label: str, files: list[str],
                    provenance: str) -> dict[str, Any]:
    """Every run carries one selection identity. Two lists with different
    bytes are two different selections even if they are both 'shard 1'."""

    blob = ("\n".join(files) + "\n").encode("utf-8")
    return {
        "source": source,
        "label": label,
        "provenance": provenance,
        "file_count": len(files),
        "sha256": _sha256_bytes(blob),
        "files": files,
    }


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #

def preflight(repo: Path, selection: dict[str, Any], *, require_head: str | None,
              require_dirty_false: bool, require_selection_sha256: str | None,
              require_file_hashes: Path | None) -> tuple[list[str], dict[str, Any]]:
    """Refuse BEFORE claiming execution. Problems are returned; the caller
    writes them to RESULT.json with verdict ``refused`` and exits non-zero."""

    problems: list[str] = []
    facts: dict[str, Any] = {"facilities": {}, "file_hashes": {}}
    if not (repo / ".git").exists():
        problems.append(f"not a git repository: {repo}")
        return problems, facts
    info = git_info(repo)
    facts["git"] = info
    if require_head and info["head"] != require_head:
        problems.append(f"repo head {info['head']} != pinned {require_head}")
    if require_dirty_false and info["dirty"]:
        problems.append(f"repo is dirty ({len(info['status_short'])} entries); "
                        "a pinned reproduction requires a clean tree")
    for script in _OPS_SCRIPTS:
        if not (repo / script).exists():
            problems.append(f"missing CI instrument script: {script}")
    if os.name != "posix":
        problems.append("unsupported platform: ops/pytest_watchdog.py requires POSIX "
                        "process groups (os.name != posix)")
    for name in selection["files"]:
        if not (repo / name).is_file():
            problems.append(f"selection references missing file: {name}")
    facts["facilities"]["xvfb"] = _xvfb_status()
    if require_selection_sha256 and selection["sha256"] != require_selection_sha256:
        problems.append(f"selection sha256 {selection['sha256']} != pinned "
                        f"{require_selection_sha256}")
    for name in selection["files"]:
        if (repo / name).is_file():
            facts["file_hashes"][name] = sha256_file(repo / name)
    if require_file_hashes is not None:
        pinned = json.loads(require_file_hashes.read_text(encoding="utf-8"))
        for name, digest in (pinned.get("selection_file_hashes")
                             or pinned.get("file_hashes") or {}).items():
            actual = facts["file_hashes"].get(name)
            if actual is None:
                problems.append(f"pinned file no longer in selection: {name}")
            elif actual != digest:
                problems.append(f"source changed since pin: {name} "
                                f"{actual} != {digest}")
    return problems, facts


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def _parse_junit(path: Path, timing_failed: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """Per-node outcome + first error text from the run's junit sink. The junit
    file is an EVIDENCE SINK added to the CI argv (recorded as a delta); it
    selects nothing, orders nothing and cannot change a verdict.

    pytest 9's xunit2 testcase carries a dotted ``classname`` and no ``file``
    attribute, so the nodeid is reconstructed (``tests.test_x`` →
    ``tests/test_x.py``) and, when the reconstruction does not hit a node the
    timing manifest actually saw fail, matched by test-name suffix against
    that manifest -- never guessed silently: a reconstruction that matches
    nothing is dropped and surfaces as a junit/timing inconsistency."""

    timing_failed = timing_failed or {}
    results: dict[str, dict[str, str]] = {}
    if not path.exists():
        return results
    root = ElementTree.parse(path).getroot()
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        if not classname or not name:
            continue
        nodeid = f"{classname.replace('.', '/')}.py::{name}"
        if nodeid not in timing_failed:
            suffix_matches = [known for known in timing_failed
                              if known.endswith(f"::{name}")]
            if len(suffix_matches) == 1:
                nodeid = suffix_matches[0]
        outcome = "passed"
        detail = ""
        for kind in ("failure", "error", "skipped"):
            element = case.find(kind)
            if element is not None:
                outcome = "error" if kind == "error" else kind
                text = (element.text or "").strip().splitlines()
                excerpt = [line for line in text if line.strip()][:12]
                detail = "\n".join(excerpt)
                break
        results[nodeid] = {"outcome": outcome, "first_error": detail}
    return results


def execute_run(repo: Path, run_dir: Path, profile_root: Path, python: str,
                selection: dict[str, Any], *, phase_timeout: float,
                wall_budget: float | None, node_budgets: bool,
                extra_note: str | None) -> dict[str, Any]:
    """Spawn the CI shard command and NEVER soften its exit. Raw output goes to
    unmodified files; the watchdog's completion/interruption/stall verdict and
    the timing manifest's own exitstatus are cross-checked against the child's
    real exit code, and any disagreement is itself a red result."""

    run_dir.mkdir(parents=True, exist_ok=True)
    watchdog_dir = run_dir / "watchdog"
    timing_path = run_dir / "timing.json"
    junit_path = run_dir / "junit.xml"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    home = profile_root / f"home-{stamp}-{os.getpid()}"
    basetemp = profile_root / f"basetemp-{stamp}-{os.getpid()}"
    home.mkdir(parents=True, exist_ok=True)
    basetemp.mkdir(parents=True, exist_ok=True)

    command = [python, str(Path("ops") / "pytest_watchdog.py"),
               "--phase-timeout", str(phase_timeout),
               "--output", str(watchdog_dir)]
    if node_budgets and (repo / "ops" / "shard_weights.json").exists():
        command += ["--node-budgets", str(Path("ops") / "shard_weights.json")]
    command += ["--", python, str(Path("ops") / "pytest_timing.py"),
                "--output", str(timing_path), "--",
                "-q", "--tb=short", "-p", "no:cacheprovider",
                "--junitxml", str(junit_path), "--basetemp", str(basetemp),
                *selection["files"]]

    env = _clean_pytest_env(dict(os.environ))
    env["VOOL_HOME"] = str(home)
    started = time.monotonic()
    with (run_dir / "run-stdout.txt").open("wb") as out_handle, \
            (run_dir / "run-stderr.txt").open("wb") as err_handle:
        process = subprocess.Popen(
            command, cwd=str(repo), env=env, stdout=out_handle, stderr=err_handle,
        )
        interruption = None
        while process.poll() is None:
            if wall_budget is not None and time.monotonic() - started > wall_budget:
                interruption = "wall-budget-exceeded"
                # Only the process THIS tool started, by its recorded pid, is
                # ever signalled; the watchdog owns and stops its own group.
                process.send_signal(signal.SIGINT)
                deadline = time.monotonic() + 20
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.2)
                if process.poll() is None:
                    process.terminate()
                break
            time.sleep(0.2)
        exit_code = process.wait()
    wall_seconds = round(time.monotonic() - started, 3)

    watchdog_result: dict[str, Any] = {}
    if (watchdog_dir / "result.json").exists():
        watchdog_result = json.loads(
            (watchdog_dir / "result.json").read_text(encoding="utf-8"))
    timing: dict[str, Any] = {}
    if timing_path.exists():
        try:
            timing = json.loads(timing_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            timing = {"unparseable": True}
    timing_failed = {
        entry["nodeid"]: entry
        for entry in (timing.get("failed_nodes") or [])
    }
    junit = _parse_junit(junit_path, timing_failed)

    failures = {node: detail for node, detail in junit.items()
                if detail["outcome"] in {"failure", "error"}}
    inconsistencies: list[str] = []
    if timing and not timing.get("unparseable"):
        if timing.get("exitstatus") is not None and int(timing["exitstatus"]) != exit_code:
            inconsistencies.append(
                f"timing manifest exitstatus {timing['exitstatus']} != child exit {exit_code}")
        junit_fail_ids = {n for n, d in junit.items() if d["outcome"] in {"failure", "error"}}
        if junit and junit_fail_ids != set(timing_failed):
            inconsistencies.append(
                f"junit failed/error nodes ({len(junit_fail_ids)}) != timing failed "
                f"nodes ({len(timing_failed)})")
    if watchdog_result and not watchdog_result.get("complete", True) and exit_code in (0,):
        inconsistencies.append("watchdog says incomplete but child exited 0")

    summary = (timing.get("summary") or {}) if timing else {}
    files_summary = (timing.get("files")
                     or (summary.get("files") if isinstance(summary, dict) else None)
                     or {})
    outcome_totals = {
        key: sum(int(entry.get(key, 0)) for entry in files_summary.values())
        for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")
    }
    if exit_code == 2:
        verdict = "collection-error"
    elif interruption == "wall-budget-exceeded" and exit_code in (130, -2):
        verdict = "interrupted-by-wall-budget"
    elif watchdog_result.get("timed_out"):
        verdict = "stalled-phase-timeout"
    elif watchdog_result.get("interrupted"):
        verdict = "interrupted"
    elif inconsistencies:
        verdict = "harvest-inconsistent"
    elif exit_code == 0:
        verdict = "completed-clean"
    else:
        verdict = "completed-with-failures"

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "verdict": verdict,
        "exit_code": exit_code,
        "complete": bool(watchdog_result.get("complete")) and bool(timing.get("complete")),
        "watchdog": watchdog_result,
        "timing_summary": {
            "complete": timing.get("complete"),
            "exitstatus": timing.get("exitstatus"),
            "session": timing.get("session"),
            "outcome_totals": outcome_totals,
        },
        "failed_nodes": {
            node: {
                "first_error": detail["first_error"],
                "timing": timing_failed.get(node),
            }
            for node, detail in sorted(failures.items())
        },
        "fail_counts": {"failed_nodes_recorded": len(failures)},
        "child_pid": process.pid,
        "command": command,
        "argv_deltas_vs_ci": [
            f"--junitxml {junit_path} (evidence sink: per-node outcomes + first error text)",
            f"--basetemp {basetemp} (scratch isolation; CI relies on the runner's tmp)",
            "xvfb-run wrapper absent on this platform (recorded in facilities)",
        ],
        "environment_note": extra_note,
        "wall_seconds": wall_seconds,
        "vool_home": str(home),
        "basetemp": str(basetemp),
        "inconsistencies": inconsistencies,
    }
    (run_dir / "RESULT.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #

def compare_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable baseline/candidate diff of failed nodes AND first
    errors. Selections must be byte-identical (same sha256); heads differ by
    design and are both recorded. Environment deltas are listed as FACTS --
    nothing here exonerates a failure or declares 'no regression'."""

    problems: list[str] = []
    base_sel = baseline.get("selection", {})
    cand_sel = candidate.get("selection", {})
    if base_sel.get("sha256") != cand_sel.get("sha256"):
        problems.append(
            f"selection mismatch: baseline {base_sel.get('sha256')} vs candidate "
            f"{cand_sel.get('sha256')} -- different selections are never compared")
    base_failed = set(baseline.get("failed_nodes", {}))
    cand_failed = set(candidate.get("failed_nodes", {}))
    both = sorted(base_failed & cand_failed)
    only_base = sorted(base_failed - cand_failed)
    only_cand = sorted(cand_failed - base_failed)

    def first_error(run: dict[str, Any], node: str) -> str:
        return (run.get("failed_nodes", {}).get(node, {}) or {}).get("first_error", "")

    return {
        "schema": SCHEMA + ".compare",
        "problems": problems,
        "baseline": {
            "label": baseline.get("label"),
            "head": (baseline.get("git") or {}).get("head"),
            "verdict": baseline.get("verdict"),
            "exit_code": baseline.get("exit_code"),
            "outcome_totals": (baseline.get("timing_summary") or {}).get("outcome_totals"),
            "selection_sha256": base_sel.get("sha256"),
        },
        "candidate": {
            "label": candidate.get("label"),
            "head": (candidate.get("git") or {}).get("head"),
            "verdict": candidate.get("verdict"),
            "exit_code": candidate.get("exit_code"),
            "outcome_totals": (candidate.get("timing_summary") or {}).get("outcome_totals"),
            "selection_sha256": cand_sel.get("sha256"),
        },
        "failed_in_both": [
            {"nodeid": node,
             "baseline_first_error": first_error(baseline, node),
             "candidate_first_error": first_error(candidate, node)}
            for node in both
        ],
        "failed_only_in_baseline": [
            {"nodeid": node, "first_error": first_error(baseline, node)}
            for node in only_base
        ],
        "failed_only_in_candidate": [
            {"nodeid": node, "first_error": first_error(candidate, node)}
            for node in only_cand
        ],
        "environment_facts": {
            "baseline_python": (baseline.get("environment") or {}).get("python_version"),
            "candidate_python": (candidate.get("environment") or {}).get("python_version"),
            "baseline_packages": (baseline.get("environment") or {}).get("packages"),
            "candidate_packages": (candidate.get("environment") or {}).get("packages"),
            "note": "environment deltas are recorded facts; they neither exonerate "
                    "a failure nor establish a no-regression verdict",
        },
        "attribution_note": (
            "node-level deltas are UNATTRIBUTED by this comparison: each only-in-"
            "candidate failure needs its own cause established (code change, "
            "environment, order, or flake) before it is called anything"
        ),
    }


# --------------------------------------------------------------------------- #
# Command assembly
# --------------------------------------------------------------------------- #

def _load_explicit_selection(path: Path, label: str) -> dict[str, Any]:
    files = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    return build_selection(
        source="explicit-filelist",
        label=label,
        files=files,
        provenance=(
            "historical ordered file list pinned by sha256 at run time; this is "
            "NOT the live canonical collection of this machine (which may drift "
            "on missing optional dependencies) and never stands in for it"
        ),
    )


def command_run(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    run_dir = args.artifacts / args.label
    run_dir.mkdir(parents=True, exist_ok=True)

    selection: dict[str, Any]
    collection: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    resolution: dict[str, Any] = {}
    if args.selection_explicit:
        selection = _load_explicit_selection(Path(args.selection_explicit), args.selection_label)
    else:
        collection = canonical_collection(repo, run_dir / "collect", args.python,
                                          profile_root=args.profiles)
        if args.shard is None:
            selection = build_selection(
                source="canonical-shard",
                label=args.selection_label,
                files=collection["linux_files"],
                provenance="live canonical collection of THIS machine; NO shard "
                           "partition (whole linux selection)",
            )
        else:
            resolution = resolve_canonical_shard(
                repo, run_dir / "collect", args.python,
                shard=args.shard, shards=args.shards)
            selection = build_selection(
                source="canonical-shard",
                label=args.selection_label,
                files=resolution["files"],
                provenance=(
                    f"canonical collection ({collection['linux_files']} linux files) "
                    f"partitioned by ops/shard_resolver.py with --platform linux "
                    f"(CI partition); resolver exit {resolution['resolver_command_exit']}"
                ),
            )
            if resolution["resolver_command_exit"] != 0:
                (run_dir / "RESULT.json").write_text(json.dumps({
                    "schema": SCHEMA, "verdict": "refused",
                    "refusal": "shard resolver failed; no execution claimed",
                    "resolver": resolution,
                }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                return EXIT_REFUSED_PREFLIGHT
    if args.prefix is not None:
        if not 1 <= args.prefix <= len(selection["files"]):
            (run_dir / "RESULT.json").write_text(json.dumps({
                "schema": SCHEMA, "verdict": "refused",
                "refusal": f"--prefix {args.prefix} outside 1..{len(selection['files'])}",
            }, indent=2) + "\n", encoding="utf-8")
            return EXIT_REFUSED_PREFLIGHT
        selection = dict(selection)
        selection["files"] = selection["files"][:args.prefix]
        selection["file_count"] = args.prefix
        selection["sha256"] = _sha256_bytes(
            ("\n".join(selection["files"]) + "\n").encode("utf-8"))
        selection["label"] = f"{selection['label']}[prefix-{args.prefix}]"
        selection["provenance"] += f"; bounded to first {args.prefix} files IN ORDER"

    (run_dir / "selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "selection.txt").write_text(
        "\n".join(selection["files"]) + "\n", encoding="utf-8")

    problems, facts = preflight(
        repo, selection,
        require_head=args.require_head,
        require_dirty_false=not args.allow_dirty,
        require_selection_sha256=args.require_selection_sha256,
        require_file_hashes=(Path(args.require_file_hashes)
                             if args.require_file_hashes else None),
    )
    environment = environment_fingerprint(args.python)
    fingerprints = {
        "environment": environment,
        "git": facts.get("git"),
        "facilities": facts.get("facilities"),
        "runner_script_sha256": sha256_file(Path(__file__).resolve()),
        "instruments": {
            script: sha256_file(repo / script)
            for script in _OPS_SCRIPTS if (repo / script).exists()
        },
        "shard_weights_sha256": (sha256_file(repo / "ops" / "shard_weights.json")
                                 if (repo / "ops" / "shard_weights.json").exists() else None),
        "selection_file_hashes": facts.get("file_hashes"),
        "head_identity": {
            "note": (
                "This reproduction pins an exact repository head (branch head in "
                "the local archive). A GitHub CI run on a pull request executes "
                "GitHub's SYNTHETIC MERGE COMMIT of the PR branch into the base "
                "branch -- a different commit identity even when the resulting "
                "test selection is numerically identical. Local evidence never "
                "claims the CI merge commit's identity."
            ),
        },
    }
    if args.selection_explicit:
        pinned = Path(args.selection_explicit)
        fingerprints["selection_input_sha256"] = sha256_file(pinned)
    (run_dir / "fingerprints.json").write_text(
        json.dumps(fingerprints, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result: dict[str, Any]
    if problems:
        result = {
            "schema": SCHEMA, "verdict": "refused",
            "refusals": problems, "selection": selection,
            "label": args.label, "git": facts.get("git"),
            "environment": environment,
            "executed": False,
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"REFUSED ({len(problems)} problem(s)); no execution claimed",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return (EXIT_REFUSED_PIN if any("pinned" in p or "changed since pin" in p
                                        for p in problems) else EXIT_REFUSED_PREFLIGHT)

    payload = execute_run(
        repo, run_dir, Path(args.profiles), args.python, selection,
        phase_timeout=args.phase_timeout, wall_budget=args.wall_budget,
        node_budgets=not args.no_node_budgets, extra_note=args.note,
    )
    payload.update({
        "label": args.label,
        "selection": selection,
        "collection": collection or None,
        "manifest": manifest or None,
        "resolution": resolution or None,
        "git": facts.get("git"),
        "environment": environment,
        "facilities": facts.get("facilities"),
        "executed": True,
    })
    payload["head_identity"] = {**fingerprints["head_identity"],
                                "repo_head": facts.get("git", {}).get("head"),
                                "repo_tree": facts.get("git", {}).get("tree")}
    if args.selection_explicit:
        payload["head_identity"]["selection_input_sha256"] = fingerprints[
            "selection_input_sha256"]
    (run_dir / "RESULT.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"label={args.label} verdict={payload['verdict']} exit={payload['exit_code']} "
          f"complete={payload['complete']} failed_nodes={payload['fail_counts']['failed_nodes_recorded']}")
    if payload["inconsistencies"]:
        return EXIT_HARVEST_INCONSISTENT
    return payload["exit_code"]


def command_collect(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    collection = canonical_collection(repo, args.artifacts, args.python,
                                      profile_root=args.profiles)
    manifest = canonical_manifest(repo, args.artifacts, args.python,
                                  profile_root=args.profiles)
    payload = {"schema": SCHEMA, "collection": collection, "manifest": manifest,
               "git": git_info(repo), "environment": environment_fingerprint(args.python)}
    (args.artifacts / "COLLECTION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"collection": collection, "manifest": manifest}, indent=2))
    return 0 if (collection["collection_command_exit"] == 0
                 and manifest["manifest_command_exit"] == 0) else 1


def command_compare(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    comparison = compare_runs(baseline, candidate)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "failed_in_both": len(comparison["failed_in_both"]),
        "failed_only_in_baseline": len(comparison["failed_only_in_baseline"]),
        "failed_only_in_candidate": len(comparison["failed_only_in_candidate"]),
        "problems": comparison["problems"],
    }
    print(json.dumps(summary, indent=2))
    return 1 if comparison["problems"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(".").resolve(),
                        help="repository the shard leg runs against (default: cwd)")
    parser.add_argument("--artifacts", type=Path, default=Path("local-ci-evidence"),
                        help="directory (OUTSIDE the repo) receiving all evidence")
    parser.add_argument("--profiles", type=Path, default=Path("local-ci-profiles"),
                        help="directory for isolated per-run VOOL_HOME/basetemp")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter for collection and the shard command")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="one-command pinned shard execution")
    run.add_argument("--label", required=True)
    run.add_argument("--selection-explicit",
                     help="ordered file list (historical evidence); canonical "
                          "collection is used when absent")
    run.add_argument("--selection-label", default="unlabeled-selection")
    run.add_argument("--shard", type=int)
    run.add_argument("--shards", type=int, default=10)
    run.add_argument("--prefix", type=int,
                     help="run only the first N files of the selection, IN ORDER")
    run.add_argument("--phase-timeout", type=float, default=600.0,
                     help="watchdog per-phase bound (CI: 600)")
    run.add_argument("--wall-budget", type=float, default=None,
                     help="overall wall budget; on exceed the run is interrupted "
                          "(only this tool's own recorded child is signalled)")
    run.add_argument("--no-node-budgets", action="store_true")
    run.add_argument("--require-head")
    run.add_argument("--require-selection-sha256")
    run.add_argument("--require-file-hashes", help="file_hashes JSON from a prior "
                    "run's fingerprints.json (changed-source detection)")
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--note")
    run.set_defaults(func=command_run)

    collect = sub.add_parser("collect", help="canonical collection + manifest only")
    collect.set_defaults(func=command_collect)

    compare = sub.add_parser("compare", help="baseline/candidate failed-node diff")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--output", required=True)
    compare.set_defaults(func=command_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.artifacts = args.artifacts.resolve()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    args.profiles = args.profiles.resolve()
    args.profiles.mkdir(parents=True, exist_ok=True)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

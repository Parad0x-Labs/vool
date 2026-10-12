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
  * FAIL-CLOSED HARVEST: a ``completed-*`` verdict is earned from evidence,
    never inferred from the child's exit code alone. It requires the watchdog
    result, the timing manifest and the JUnit file to be present, parseable,
    complete and in agreement with the child's exit (exitstatus, failed node
    set, error count), and the JUnit must account for every node the timing
    manifest saw start, file by file, with the same outcomes and from the same
    session (case coverage, carried in RESULT ``coverage`` so compare can check
    it), node by node: the runner's own node ledger (a passive ``-p`` evidence
    sink) names every collected and started node and its per-phase outcomes,
    and the JUnit node set and outcomes must equal it -- equal counts with
    different nodes never pass. Missing or malformed evidence is ``harvest-failed``,
    disagreeing evidence is ``harvest-inconsistent`` (both exit 92), missing
    totals are null (unknown), never zero. Every pytest exit has an explicit
    mapping; the runner exits 0 only for a complete clean run.
  * REFUSALS: missing files, an empty selection, a failed or timed-out
    canonical collection or shard resolution, missing scripts, non-POSIX
    platforms and broken identity pins are refused BEFORE any execution is
    claimed, always with a RESULT.json receipt. Facility gaps (Xvfb,
    Linux-only sandboxing, uninstalled optional packages) are recorded as
    facts -- never used to exonerate a failure, never silently dropped.
  * BOUNDS: canonical collection and shard resolution run under timeouts
    (``--collect-timeout``); ``--wall-budget`` covers collection, resolution
    and execution together. The fixed-timeout identity probes (git,
    interpreter and package fingerprint) are bounded separately; every phase's
    seconds are recorded in RESULT ``timings``.
  * COMPARE: only two executed, complete, consistent receipts of the SAME
    selection are compared; anything else is refused with a non-zero exit and
    no node diff is presented.

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
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ElementTree
from datetime import datetime
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

#: Exit codes this tool owns. A refusal never claims execution; a harvest that
#: cannot prove what ran (missing, malformed or disagreeing evidence) is 92.
EXIT_REFUSED_PREFLIGHT = 90
EXIT_REFUSED_PIN = 91
EXIT_HARVEST_INCONSISTENT = 92
EXIT_HARVEST_FAILED = EXIT_HARVEST_INCONSISTENT

#: Schema of the timing manifest ops/pytest_timing.py writes; anything else is
#: malformed evidence.
TIMING_SCHEMA = "vool.pytest-timing.v1"

#: The only verdicts compare accepts and the only ones that describe a pytest
#: session that ran to its end with all evidence in agreement.
COMPLETED_VERDICTS = ("completed-clean", "completed-with-failures")

#: Explicit verdict -> runner exit mapping. 0 belongs to completed-clean alone.
#: ``unknown-exit`` propagates the child's code (non-zero by construction).
RUNNER_EXIT_BY_VERDICT = {
    "completed-clean": 0,
    "completed-with-failures": 1,
    "collection-error": 2,
    "session-interrupted": 2,
    "internal-error": 3,
    "usage-error": 4,
    "no-tests-collected": 5,
    "stalled-phase-timeout": 124,
    "interrupted": 130,
    "interrupted-by-wall-budget": 130,
    "harvest-failed": EXIT_HARVEST_FAILED,
    "harvest-inconsistent": EXIT_HARVEST_INCONSISTENT,
}

#: First-error excerpts keep this many non-empty lines; longer texts are
#: flagged as truncated so equality beyond the excerpt is never implied.
FIRST_ERROR_LINES = 12

#: Default bound for the canonical collection (and the collect subcommand's
#: manifest); the shard resolver is pure data and gets a fixed short bound.
DEFAULT_COLLECT_TIMEOUT = 900.0
RESOLVER_TIMEOUT = 120.0

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


def _run_bounded(command: list[str], *, cwd: Path, env: dict[str, str],
                 stdout_path: Path, stderr_path: Path, timeout: float) -> dict[str, Any]:
    """Run one pre-execution step in its OWN new session under a hard bound.

    On expiry the step's own process group (its recorded pid is the group id)
    is stopped -- TERM, then KILL after a grace -- so a hung import cannot
    leave a grandchild behind. Nothing outside that group is ever signalled.
    ``exit`` is None when the bound expired: no exit code is invented."""

    started = time.monotonic()
    timed_out = False
    with stdout_path.open("wb") as out_handle, stderr_path.open("wb") as err_handle:
        process = subprocess.Popen(
            command, cwd=str(cwd), env=env, stdout=out_handle, stderr=err_handle,
            start_new_session=True,
        )
        try:
            exit_code: int | None = process.wait(timeout=max(timeout, 0.0))
        except subprocess.TimeoutExpired:
            timed_out = True
            for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    process.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
            exit_code = None
    return {
        "exit": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": round(timeout, 3),
        "seconds": round(time.monotonic() - started, 3),
        "pid": process.pid,
        "stopped": process.poll() is not None,
    }


def _read_file_list(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


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
                         profile_root: Path | None = None,
                         timeout: float = DEFAULT_COLLECT_TIMEOUT) -> dict[str, Any]:
    """Replicate the ci.yml collection step: collect-only, ``::``-line parse,
    sorted unique files, macOS routing with the drift RECORDED, not repaired.

    On a machine without every CI optional dependency some files fail import
    during collection and drop out of the parse. CI machines install the full
    extras set; a local machine that does not WILL drift, and that drift is a
    fact about the local environment -- written to ``macos_not_collected`` /
    noted in the counts, never silently patched up. Pinning a historical list
    (``--selection-explicit``) is the explicit, hashed act of reproducing a
    selection that was collected on a complete environment.

    The collection runs under ``timeout`` seconds; on expiry
    ``collection_command_exit`` is None and ``timed_out`` is true. The file
    list itself is ``linux-collected.txt`` in ``outdir`` (``linux_files`` here
    is its COUNT)."""

    outdir.mkdir(parents=True, exist_ok=True)
    env = (_isolated_collection_env(profile_root) if profile_root is not None
           else _clean_pytest_env(dict(os.environ)))
    process = _run_bounded(
        [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=repo, env=env,
        stdout_path=outdir / "collect-stdout.txt",
        stderr_path=outdir / "collect-stderr.txt",
        timeout=timeout,
    )
    stdout = (outdir / "collect-stdout.txt").read_text(encoding="utf-8", errors="replace")
    files = sorted({line.split("::", 1)[0] for line in stdout.splitlines() if "::" in line})
    macos_collected = sorted(set(files) & MACOS_ONLY_FILES)
    macos_not_collected = sorted(MACOS_ONLY_FILES - set(files))
    linux_files = [f for f in files if f not in MACOS_ONLY_FILES]
    (outdir / "linux-collected.txt").write_text("\n".join(linux_files) + "\n", encoding="utf-8")
    return {
        "collection_command_exit": process["exit"],
        "collection_timed_out": process["timed_out"],
        "collection_timeout_seconds": process["timeout_seconds"],
        "collection_seconds": process["seconds"],
        "collection_pid": process["pid"],
        "collected_files_total": len(files),
        "linux_files": len(linux_files),
        "macos_routed_out": macos_collected,
        "macos_only_not_collected_locally": macos_not_collected,
        "linux_collected_sha256": sha256_file(outdir / "linux-collected.txt"),
    }


def canonical_manifest(repo: Path, outdir: Path, python: str,
                        profile_root: Path | None = None,
                        timeout: float = DEFAULT_COLLECT_TIMEOUT) -> dict[str, Any]:
    """The verify leg's ``ops/pytest_manifest.py`` invocation, unchanged,
    under ``timeout`` seconds. Only the ``collect`` subcommand records it;
    ``run`` does not compute a manifest and its RESULT carries none, so a
    run receipt is paired by its selection sha256, never by a manifest."""

    outdir.mkdir(parents=True, exist_ok=True)
    manifest = outdir / "pytest-manifest.json"
    env = (_isolated_collection_env(profile_root) if profile_root is not None
           else _clean_pytest_env(dict(os.environ)))
    manifest.unlink(missing_ok=True)
    process = _run_bounded(
        [python, str(Path("ops") / "pytest_manifest.py"), "--repo-root", ".",
         "--output", str(manifest), "--", "-q"],
        cwd=repo, env=env,
        stdout_path=outdir / "manifest-stdout.txt",
        stderr_path=outdir / "manifest-stderr.txt",
        timeout=timeout,
    )
    payload: dict[str, Any] = {}
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    return {
        "manifest_command_exit": process["exit"],
        "manifest_timed_out": process["timed_out"],
        "manifest_seconds": process["seconds"],
        "item_count": payload.get("item_count"),
        "manifest_sha256": sha256_file(manifest) if manifest.exists() else None,
    }


def resolve_canonical_shard(repo: Path, outdir: Path, python: str, *, shard: int,
                            shards: int, timeout: float = RESOLVER_TIMEOUT) -> dict[str, Any]:
    """Compute the CI shard partition with ``--platform linux`` so the plan is
    the one automatic CI computed (the committed weights snapshot is Linux
    data; on any other sys.platform the resolver would correctly refuse it and
    silently fall back to a DIFFERENT partition -- exactly the conflation this
    tool exists to prevent). Pure data computation; nothing is executed. The
    resolver runs under ``timeout`` seconds (exit None on expiry)."""

    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / f"shard-{shard}-files.txt"
    output.unlink(missing_ok=True)
    env = _clean_pytest_env(dict(os.environ))
    process = _run_bounded(
        [python, str(Path("ops") / "shard_resolver.py"),
         "--shard", str(shard), "--shards", str(shards),
         "--files", str(outdir / "linux-collected.txt"),
         "--weights", str(Path("ops") / "shard_weights.json"),
         "--platform", "linux",
         "--output", str(output)],
        cwd=repo, env=env,
        stdout_path=outdir / "resolver-stdout.txt",
        stderr_path=outdir / "resolver-stderr.txt",
        timeout=timeout,
    )
    files = _read_file_list(output)
    stdout_lines = (outdir / "resolver-stdout.txt").read_text(
        encoding="utf-8", errors="replace").strip().splitlines()
    return {
        "resolver_command_exit": process["exit"],
        "resolver_timed_out": process["timed_out"],
        "resolver_seconds": process["seconds"],
        "resolver_pid": process["pid"],
        "shard": shard, "shards": shards,
        "resolver_platform_override": "linux (CI partition; committed snapshot is Linux data)",
        "files": files,
        "mode_line": stdout_lines[-1] if stdout_lines else "",
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
    if not selection["files"]:
        # pytest given no paths runs the configured default testpaths -- the
        # whole suite under a 0-file label. Never executed, never labelled.
        problems.append("empty selection (0 files): pytest with no paths would run "
                        "the default testpaths, not this selection")
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

class EvidenceError(Exception):
    """One evidence file is missing or malformed; the message names which and why."""


_OUTCOME_KEYS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")
_EVIDENCE_FILES = (Path("RESULT.json"), Path("timing.json"), Path("junit.xml"),
                   Path("nodes.json"), Path("watchdog") / "result.json")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _load_json_object(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise EvidenceError(f"{name} missing: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{name} malformed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceError(f"{name} malformed: top level is {type(data).__name__}, "
                            "not an object")
    return data


def load_watchdog_result(path: Path) -> dict[str, Any]:
    """The watchdog's own result.json, type-checked field by field."""

    data = _load_json_object(path, "watchdog result")
    for key, check, kind in (("complete", lambda v: isinstance(v, bool), "bool"),
                             ("timed_out", lambda v: isinstance(v, bool), "bool"),
                             ("interrupted", lambda v: isinstance(v, bool), "bool"),
                             ("exitstatus", _is_int, "int")):
        if not check(data.get(key)):
            raise EvidenceError(f"watchdog result malformed: {key}={data.get(key)!r} "
                                f"is not {kind}")
    return data


def load_timing_manifest(path: Path) -> dict[str, Any]:
    """The timing manifest, schema- and type-checked; its totals are derived
    here so a malformed per-file entry is malformed evidence, not a zero."""

    data = _load_json_object(path, "timing manifest")
    if data.get("schema") != TIMING_SCHEMA:
        raise EvidenceError(f"timing manifest malformed: schema {data.get('schema')!r} "
                            f"!= {TIMING_SCHEMA}")
    if not isinstance(data.get("complete"), bool):
        raise EvidenceError(f"timing manifest malformed: complete={data.get('complete')!r}")
    exitstatus = data.get("exitstatus")
    if exitstatus is not None and not _is_int(exitstatus):
        raise EvidenceError(f"timing manifest malformed: exitstatus={exitstatus!r}")
    if data["complete"] and exitstatus is None:
        raise EvidenceError("timing manifest malformed: complete without an exitstatus")
    if not isinstance(data.get("session"), dict):
        raise EvidenceError("timing manifest malformed: session is not an object")
    files = data.get("files")
    if not isinstance(files, dict):
        raise EvidenceError("timing manifest malformed: files is not an object")
    failed = data.get("failed_nodes")
    if not isinstance(failed, list) or not all(
            isinstance(entry, dict) and isinstance(entry.get("nodeid"), str)
            for entry in failed):
        raise EvidenceError("timing manifest malformed: failed_nodes is not a list of "
                            "{nodeid: str} entries")
    totals = {key: 0 for key in _OUTCOME_KEYS}
    for name, entry in files.items():
        if not isinstance(entry, dict):
            raise EvidenceError(f"timing manifest malformed: files[{name!r}] is not an object")
        for key in _OUTCOME_KEYS:
            value = entry.get(key, 0)
            if not _is_int(value) or value < 0:
                raise EvidenceError(f"timing manifest malformed: files[{name!r}].{key}="
                                    f"{value!r}")
            totals[key] += value
    data["_outcome_totals"] = totals
    return data


def _first_error_excerpt(element: ElementTree.Element) -> tuple[str, int]:
    lines = [line for line in (element.text or "").strip().splitlines() if line.strip()]
    return "\n".join(lines[:FIRST_ERROR_LINES]), len(lines)


def _junit_nodeid(classname: str, name: str,
                  module_to_file: dict[str, str]) -> tuple[str, bool]:
    """pytest's xunit2 testcase has a dotted ``classname`` (module, then any
    classes) and no ``file`` attribute. The module part is anchored to a file
    of THIS selection (longest dotted prefix), the rest are classes, so nested
    test classes keep their real nodeid. Unanchored names fall back to the
    plain dotted reconstruction and are reported as unanchored."""

    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        module_file = module_to_file.get(".".join(parts[:cut]))
        if module_file is not None:
            return "::".join([module_file, *parts[cut:], name]), True
    return f"{classname.replace('.', '/')}.py::{name}", False


def parse_junit(path: Path, selection_files: list[str]) -> dict[str, Any]:
    """Per-node outcome + first error text from the run's junit sink. The junit
    file is an EVIDENCE SINK added to the CI argv (recorded as a delta); it
    selects nothing, orders nothing and never decides a verdict on its own --
    it is cross-checked against the timing manifest.

    Counted per ELEMENT the way the timing manifest counts per REPORT: a
    call failure is one ``<failure>``, each setup/teardown failure one
    ``<error>`` (a test failing in call and in teardown appears as two
    testcases). Collection failures (empty classname, message ``collection
    failure``) are recorded separately; they are not test nodes."""

    if not path.is_file():
        raise EvidenceError(f"junit missing: {path.name}")
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as exc:
        raise EvidenceError(f"junit malformed: {type(exc).__name__}: {exc}") from exc
    module_to_file = {name[:-3].replace("/", "."): name
                      for name in selection_files if name.endswith(".py")}
    nodes: dict[str, dict[str, Any]] = {}
    collection_errors: list[dict[str, Any]] = []
    unanchored: list[str] = []
    counts = {"testcases": 0, "failure": 0, "error": 0, "skipped": 0}
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        marks = [child for child in case if child.tag in ("failure", "error", "skipped")]
        if not classname:
            # A collection failure (dotted module as name) or the nameless
            # testcase pytest writes for an interrupted session.
            for child in marks:
                if child.tag == "error" and child.get("message") == "collection failure":
                    excerpt, total = _first_error_excerpt(child)
                    collection_errors.append({
                        "module": name, "first_error": excerpt,
                        "first_error_lines": total,
                        "first_error_truncated": total > FIRST_ERROR_LINES,
                    })
            continue
        if not name:
            continue
        counts["testcases"] += 1
        nodeid, anchored = _junit_nodeid(classname, name, module_to_file)
        if not anchored:
            # Never guessed onto a real node: an unanchored case keeps its plain
            # reconstruction and fails identity matching if it names nothing real.
            unanchored.append(nodeid)
        entry = nodes.setdefault(nodeid, {"failure": 0, "error": 0, "skipped": 0, "xfail": 0,
                                          "setup_error": 0, "unclassified_error": 0,
                                          "testcases": 0, "first_error": "",
                                          "first_error_lines": 0})
        entry["testcases"] += 1
        for child in marks:
            entry[child.tag] += 1
            counts[child.tag] += 1
            message = child.get("message") or ""
            if child.tag == "skipped" and child.get("type") == "pytest.xfail":
                entry["xfail"] += 1
            if child.tag == "error":
                if message.startswith("failed on setup"):
                    entry["setup_error"] += 1
                elif not message.startswith("failed on teardown"):
                    entry["unclassified_error"] += 1
            if child.tag in ("failure", "error") and not entry["first_error_lines"]:
                entry["first_error"], entry["first_error_lines"] = _first_error_excerpt(child)
    suite_timestamps = [suite.get("timestamp") for suite in root.iter("testsuite")]
    return {
        "nodes": nodes,
        "files": junit_file_counts(nodes),
        "suite_timestamps": suite_timestamps,
        "failure_nodes": {node for node, entry in nodes.items() if entry["failure"]},
        "failure_elements": counts["failure"],
        "error_elements": counts["error"],
        "skipped_elements": counts["skipped"],
        "testcases": counts["testcases"],
        "collection_errors": collection_errors,
        "unanchored_nodeids": unanchored[:20],
        "unanchored_count": len(unanchored),
    }


#: The per-file case counts both instruments must agree on. ``pass`` is a call
#: that ran and passed (timing: passed + xpassed); the rest are per REPORT.
COVERAGE_KEYS = ("nodes", "pass", "failure", "error", "skip", "xfail")

#: A JUnit suite and the timing manifest both stamp the start of the SAME
#: pytest session; further apart than this, the JUnit is from another session.
SESSION_TIE_SECONDS = 300.0


def junit_file_counts(nodes: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    """JUnit evidence per test file, counted the way the timing manifest counts.

    ``nodes`` is DISTINCT node ids (a test failing in call and in teardown is
    two testcase rows but one node); ``failure``/``error``/``skip``/``xfail``
    are per element (per report); ``pass`` counts nodes whose call ran and
    passed -- no failure, no skip/xfail, no setup error (a teardown error after
    a passing call still counts the pass, as pytest's report does)."""

    files: dict[str, dict[str, int]] = {}
    for nodeid, entry in nodes.items():
        counts = files.setdefault(nodeid.split("::", 1)[0], dict.fromkeys(COVERAGE_KEYS, 0))
        counts["nodes"] += 1
        counts["failure"] += entry["failure"]
        counts["error"] += entry["error"]
        counts["xfail"] += entry["xfail"]
        counts["skip"] += entry["skipped"] - entry["xfail"]
        if not (entry["failure"] or entry["skipped"] or entry["setup_error"]
                or entry["unclassified_error"]):
            counts["pass"] += 1
    return files


def _timing_file_counts(entry: dict[str, Any]) -> dict[str, int]:
    return {
        "nodes": entry.get("started_nodes", 0),
        "pass": entry.get("passed", 0) + entry.get("xpassed", 0),
        "failure": entry.get("failed", 0),
        "error": entry.get("errors", 0),
        "skip": entry.get("skipped", 0),
        "xfail": entry.get("xfailed", 0),
    }


def _parse_suite_epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment.timestamp() if moment.tzinfo is not None else None


#: Node identity evidence. The timing manifest counts passing nodes but does not
#: name them, so the runner adds its own passive evidence sink to the session
#: (``-p``, recorded as an argv delta): collected and started node ids and every
#: report's (phase, outcome, xfail) per node. It selects, skips and reorders
#: nothing, and it comes from the runner, so it works on any target head.
NODE_LEDGER_SCHEMA = "vool.local-ci-node-ledger.v1"
NODE_LEDGER_FILE = "nodes.json"
NODE_LEDGER_MODULE = "vool_local_ci_node_ledger"
NODE_LEDGER_ENV = "VOOL_LOCAL_CI_NODE_LEDGER"
NODE_LEDGER_SOURCE = '''"""Evidence sink written by ops/local_ci_reproduction.py for ONE pytest session:
collected and started node ids and every report's (phase, outcome, xfail) per
node. Selects, skips and reorders nothing."""

import json
import os

_PATH = os.environ.get("VOOL_LOCAL_CI_NODE_LEDGER")
_STATE = {"schema": "vool.local-ci-node-ledger.v1", "complete": False, "exitstatus": None,
          "collected": [], "started": [], "reports": {}}


def _write():
    if not _PATH:
        return
    temporary = _PATH + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(_STATE, handle)
    os.replace(temporary, _PATH)


def pytest_collection_finish(session):
    _STATE["collected"] = [item.nodeid for item in session.items]
    _write()


def pytest_runtest_logstart(nodeid, location):
    _STATE["started"].append(nodeid)


def pytest_runtest_logreport(report):
    _STATE["reports"].setdefault(report.nodeid, []).append(
        [report.when, report.outcome, hasattr(report, "wasxfail")])


def pytest_sessionfinish(session, exitstatus):
    _STATE["complete"] = True
    _STATE["exitstatus"] = int(exitstatus)
    _write()
'''

_SIGNATURE_KEYS = ("pass", "failure", "error", "skip", "xfail")


def load_node_ledger(path: Path) -> dict[str, Any]:
    """The node ledger, schema- and type-checked."""

    data = _load_json_object(path, "node ledger")
    if data.get("schema") != NODE_LEDGER_SCHEMA:
        raise EvidenceError(f"node ledger malformed: schema {data.get('schema')!r} != "
                            f"{NODE_LEDGER_SCHEMA}")
    if not isinstance(data.get("complete"), bool):
        raise EvidenceError(f"node ledger malformed: complete={data.get('complete')!r}")
    if data["complete"] and not _is_int(data.get("exitstatus")):
        raise EvidenceError(f"node ledger malformed: exitstatus={data.get('exitstatus')!r}")
    for key in ("collected", "started"):
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise EvidenceError(f"node ledger malformed: {key} is not a list of node ids")
    reports = data.get("reports")
    if not isinstance(reports, dict) or not all(
            isinstance(node, str) and isinstance(entries, list) and all(
                isinstance(entry, list) and len(entry) == 3
                and entry[0] in ("setup", "call", "teardown")
                and entry[1] in ("passed", "failed", "skipped")
                and isinstance(entry[2], bool) for entry in entries)
            for node, entries in reports.items()):
        raise EvidenceError("node ledger malformed: reports is not {nodeid: "
                            "[[phase, outcome, xfail], ...]}")
    return data


def _ledger_signature(entries: list[list[Any]]) -> dict[str, int]:
    """What one node's reports must look like in JUnit: a call failure is a
    ``<failure>``, each setup/teardown failure an ``<error>``, a skipped report
    a ``<skipped>`` (``pytest.xfail`` when it is an xfail), a passing call a pass."""

    signature = dict.fromkeys(_SIGNATURE_KEYS, 0)
    for phase, outcome, xfail in entries:
        if outcome == "failed":
            signature["failure" if phase == "call" else "error"] += 1
        elif outcome == "skipped":
            signature["xfail" if xfail else "skip"] += 1
        elif phase == "call":
            signature["pass"] += 1
    return signature


def _junit_signature(entry: dict[str, Any]) -> dict[str, int]:
    passed = not (entry["failure"] or entry["skipped"] or entry["setup_error"]
                  or entry["unclassified_error"])
    return {"pass": int(passed), "failure": entry["failure"], "error": entry["error"],
            "skip": entry["skipped"] - entry["xfail"], "xfail": entry["xfail"]}


def reconcile_identity(junit: dict[str, Any], ledger: dict[str, Any] | None,
                       timing: dict[str, Any]) -> dict[str, Any]:
    """Node IDENTITY: every node the session started must appear in the JUnit
    under its own node id (classname + name, anchored to the selection) with
    the same per-phase outcome signature, and the JUnit may name nothing else.
    Equal counts with different nodes never reconcile. Without a complete
    ledger there is no identity evidence, and that is itself a reason."""

    reasons: list[str] = []
    if ledger is None:
        return {"verified": False, "reasons": ["node ledger missing or malformed: node "
                                               "identity cannot be verified"]}
    if not ledger["complete"]:
        reasons.append("node ledger incomplete: the session did not reach sessionfinish")
    started, collected = ledger["started"], ledger["collected"]
    started_set, collected_set = set(started), set(collected)
    duplicates = sorted({node for node in started if started.count(node) > 1})
    junit_nodes = junit["nodes"]
    junit_set = set(junit_nodes)
    missing = sorted(started_set - junit_set)
    unexpected = sorted(junit_set - started_set)
    not_started = sorted(collected_set - started_set)
    not_collected = sorted(started_set - collected_set)
    mismatched = []
    for node in sorted(started_set & junit_set):
        expected = _ledger_signature(ledger["reports"].get(node, []))
        observed = _junit_signature(junit_nodes[node])
        if expected != observed:
            mismatched.append({"nodeid": node, "ledger": expected, "junit": observed})
    if missing:
        reasons.append(f"{len(missing)} started node(s) have no junit case, e.g. {missing[:3]}")
    if unexpected:
        reasons.append(f"{len(unexpected)} junit case(s) name a node that never started, "
                       f"e.g. {unexpected[:3]}")
    if mismatched:
        reasons.append(f"{len(mismatched)} node(s) carry a different outcome in junit than in "
                       f"the session's reports, e.g. {json.dumps(mismatched[0], sort_keys=True)}")
    if not_started or not_collected:
        reasons.append(f"ledger collected and started node sets differ: not started "
                       f"{not_started[:3]}, not collected {not_collected[:3]}")
    if duplicates:
        reasons.append(f"node(s) started more than once: {duplicates[:3]}")
    if len(started) != sum(entry.get("started_nodes", 0) for entry in timing["files"].values()):
        reasons.append(f"ledger started {len(started)} node(s) but the timing manifest counted "
                       f"{sum(entry.get('started_nodes', 0) for entry in timing['files'].values())}")
    node_blob = ("\n".join(sorted(started_set)) + "\n").encode("utf-8")
    return {
        "verified": not reasons,
        "reasons": reasons,
        "collected": len(collected),
        "started": len(started),
        "junit_nodes": len(junit_set),
        "missing_in_junit": missing[:20],
        "unexpected_in_junit": unexpected[:20],
        "outcome_mismatches": mismatched[:20],
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        "mismatch_count": len(mismatched),
        "started_nodes_sha256": _sha256_bytes(node_blob),
    }


def reconcile_coverage(junit: dict[str, Any], timing: dict[str, Any],
                       selection_files: list[str],
                       ledger: dict[str, Any] | None) -> dict[str, Any]:
    """Case COVERAGE: does the JUnit account for every node the timing manifest
    saw start, file by file, with the same outcomes, from the same session, for
    files of this selection only? Returns the evidence plus every reason it does
    not reconcile; ``reconciled`` is true only when there is none.

    Checked per file (never a blind total): distinct nodes == started nodes,
    started == collected (no maxfail is in the CI argv, so a session that ends
    normally runs every collected node), and pass/failure/error/skip/xfail
    equal per report. The JUnit suite timestamp must lie within
    SESSION_TIE_SECONDS of the timing session start."""

    selection = set(selection_files)
    timing_files = timing["files"]
    junit_files = junit["files"]
    mismatched: list[dict[str, Any]] = []
    outside: list[str] = []
    totals = {"junit": dict.fromkeys(COVERAGE_KEYS, 0), "timing": dict.fromkeys(COVERAGE_KEYS, 0)}
    collected_total = 0
    for name in sorted(set(timing_files) | set(junit_files)):
        timing_entry = timing_files.get(name, {})
        expected = _timing_file_counts(timing_entry)
        observed = junit_files.get(name, dict.fromkeys(COVERAGE_KEYS, 0))
        collected = timing_entry.get("collected_nodes", 0)
        collected_total += collected
        for key in COVERAGE_KEYS:
            totals["timing"][key] += expected[key]
            totals["junit"][key] += observed[key]
        differences = {key: {"timing": expected[key], "junit": observed[key]}
                       for key in COVERAGE_KEYS if expected[key] != observed[key]}
        if collected != expected["nodes"]:
            differences["collected_vs_started"] = {"collected": collected,
                                                   "started": expected["nodes"]}
        if differences:
            mismatched.append({"file": name, "differences": differences})
        if (expected["nodes"] or observed["nodes"]) and name not in selection:
            outside.append(name)
    reasons: list[str] = []
    if mismatched:
        reasons.append(f"junit case coverage does not reconcile with the timing manifest in "
                       f"{len(mismatched)} file(s), e.g. {json.dumps(mismatched[0], sort_keys=True)}")
    if outside:
        reasons.append(f"evidence names test file(s) outside the selection: {outside[:5]}")
    session = timing["session"]
    if session.get("collected_total") != collected_total:
        reasons.append(f"timing session collected_total {session.get('collected_total')!r} != "
                       f"sum of per-file collected nodes {collected_total}")
    timing_start = session.get("started_at_epoch")
    suite_epochs = [_parse_suite_epoch(stamp) for stamp in junit["suite_timestamps"]]
    tie_seconds: float | None = None
    if not suite_epochs or any(epoch is None for epoch in suite_epochs):
        reasons.append(f"junit suite timestamp missing or unreadable "
                       f"({junit['suite_timestamps']!r}): cannot tie it to this session")
    elif not isinstance(timing_start, (int, float)) or isinstance(timing_start, bool):
        reasons.append("timing session has no started_at_epoch: cannot tie the junit to it")
    else:
        tie_seconds = round(max(abs(epoch - timing_start) for epoch in suite_epochs), 3)
        if tie_seconds > SESSION_TIE_SECONDS:
            reasons.append(f"junit suite started {tie_seconds}s away from the timing session "
                           "start: not the same session")
    identity = reconcile_identity(junit, ledger, timing)
    reasons.extend(identity["reasons"])
    return {
        "reconciled": not reasons,
        "identity": identity,
        "reasons": reasons,
        "collected": collected_total,
        "junit": totals["junit"],
        "timing": totals["timing"],
        "files_compared": len(set(timing_files) | set(junit_files)),
        "mismatched_files": mismatched[:20],
        "mismatched_file_count": len(mismatched),
        "files_outside_selection": outside[:20],
        "session_tie_seconds": tie_seconds,
        "rule": "per file: distinct junit nodes == timing started == collected; pass "
                "(passed+xpassed), failure, error, skip, xfail equal per report; junit "
                f"suite within {SESSION_TIE_SECONDS:g}s of the timing session start; per "
                "node: the junit node set == the ledger's started set == its collected set, "
                "with the same per-phase outcome signature",
    }


def harvest(*, child_exit: int, runner_interrupted: bool, run_dir: Path,
            selection_files: list[str]) -> dict[str, Any]:
    """Turn one run directory's evidence into a verdict, failing CLOSED.

    ``completed-*`` (and every verdict that names a pytest outcome) requires
    all three evidence files present, parseable and complete, with the
    watchdog's and the timing manifest's exitstatus equal to the child's exit,
    the junit failed-node set equal to the timing manifest's, the junit
    error count equal to the timing error total, and -- for a session that
    ended normally (exit 0, 1, 5) -- reconciled case coverage
    (``reconcile_coverage``). Missing or malformed evidence
    is ``harvest-failed``; disagreement is ``harvest-inconsistent``. Stall and
    interruption facts keep their own (never green) verdicts. Totals the
    evidence cannot give are None, never 0."""

    paths = {"watchdog": run_dir / "watchdog" / "result.json",
             "timing": run_dir / "timing.json",
             "junit": run_dir / "junit.xml",
             "ledger": run_dir / NODE_LEDGER_FILE}
    problems: list[str] = []
    evidence: dict[str, Any] = {}

    def attempt(name: str, loader: Any) -> Any:
        path = paths[name]
        record = {"path": str(path.relative_to(run_dir)),
                  "sha256": sha256_file(path) if path.is_file() else None}
        try:
            value = loader(path)
        except EvidenceError as exc:
            problems.append(str(exc))
            evidence[name] = {**record, "status": "missing" if not path.is_file()
                              else "malformed"}
            return None
        evidence[name] = {**record, "status": "ok"}
        return value

    watchdog = attempt("watchdog", load_watchdog_result)
    timing = attempt("timing", load_timing_manifest)
    timing_failed: dict[str, Any] = (
        {entry["nodeid"]: entry for entry in timing["failed_nodes"]} if timing else {})
    totals = timing.pop("_outcome_totals") if timing else None
    junit = attempt("junit", lambda path: parse_junit(path, selection_files))
    ledger = attempt("ledger", load_node_ledger)
    complete = bool(watchdog is not None and watchdog["complete"] is True
                    and timing is not None and timing["complete"] is True
                    and ledger is not None and ledger["complete"] is True)

    inconsistencies: list[str] = []
    coverage: dict[str, Any] | None = None
    if watchdog is not None:
        watchdog_exit = watchdog["exitstatus"]
        if watchdog_exit < 0:
            watchdog_exit = 128 - watchdog_exit  # the watchdog's own signal mapping
        if watchdog_exit != child_exit:
            inconsistencies.append(f"watchdog exitstatus {watchdog['exitstatus']} != "
                                   f"child exit {child_exit}")
        if not watchdog["complete"] and not (watchdog["timed_out"] or watchdog["interrupted"]):
            inconsistencies.append("watchdog says incomplete without a stall or interruption")
    if ledger is not None and ledger["complete"] and ledger["exitstatus"] != child_exit:
        inconsistencies.append(f"node ledger exitstatus {ledger['exitstatus']} != child exit "
                               f"{child_exit}")
    if timing is not None and timing["complete"]:
        if timing["exitstatus"] != child_exit:
            inconsistencies.append(f"timing manifest exitstatus {timing['exitstatus']} != "
                                   f"child exit {child_exit}")
        recorded = totals["failed"] + totals["errors"]
        if child_exit == 0 and recorded:
            inconsistencies.append(f"child exited 0 but timing recorded {recorded} "
                                   "failure/error report(s)")
        if child_exit == 1 and not recorded:
            inconsistencies.append("child exited 1 but timing recorded no failure or error")
        collected = timing["session"].get("collected_total")
        if child_exit == 5 and collected:
            inconsistencies.append(f"child exited 5 (no tests) but timing collected "
                                   f"{collected} node(s)")
        if junit is not None:
            timing_set = set(timing_failed)
            if junit["failure_nodes"] != timing_set:
                inconsistencies.append(
                    f"junit failed nodes ({len(junit['failure_nodes'])}) != timing failed "
                    f"nodes ({len(timing_set)}); timing-only "
                    f"{sorted(timing_set - junit['failure_nodes'])[:5]}, junit-only "
                    f"{sorted(junit['failure_nodes'] - timing_set)[:5]}")
            if junit["error_elements"] != totals["errors"]:
                inconsistencies.append(f"junit error elements ({junit['error_elements']}) != "
                                       f"timing errors ({totals['errors']})")
            if junit["failure_elements"] != totals["failed"]:
                inconsistencies.append(f"junit failure elements ({junit['failure_elements']}) "
                                       f"!= timing failed ({totals['failed']})")
            coverage = reconcile_coverage(junit, timing, selection_files, ledger)
            # A session that ended normally (0, 1, 5) must be FULLY accounted
            # for. An exit-2 session stopped early by design (collection error
            # or interruption): its coverage is recorded, and its verdict is
            # never completed anyway.
            if child_exit in (0, 1, 5) and not coverage["reconciled"]:
                inconsistencies.extend(coverage["reasons"])

    if runner_interrupted:
        verdict = "interrupted-by-wall-budget"
    elif watchdog is None:
        verdict = "harvest-failed"
    elif watchdog["timed_out"]:
        verdict = "stalled-phase-timeout"
    elif watchdog["interrupted"]:
        verdict = "interrupted"
    elif child_exit == 3:
        verdict = "internal-error"
    elif child_exit == 4:
        verdict = "usage-error"
    elif child_exit not in (0, 1, 2, 5):
        verdict = "unknown-exit"
    elif problems:
        verdict = "harvest-failed"
    elif not complete:
        if timing is not None and not timing["complete"]:
            problems.append("session did not reach its end: timing manifest complete=false "
                            f"with child exit {child_exit} (session aborted mid-run)")
        elif ledger is not None and not ledger["complete"]:
            problems.append("session did not reach its end: node ledger complete=false "
                            f"with child exit {child_exit}")
        else:
            problems.append("watchdog result complete=false")
        verdict = "harvest-failed"
    elif inconsistencies:
        verdict = "harvest-inconsistent"
    elif child_exit == 0:
        verdict = "completed-clean"
    elif child_exit == 1:
        verdict = "completed-with-failures"
    elif child_exit == 2:
        # pytest returns 2 both for collection errors and for an interrupted
        # session; the junit collection-failure entries tell them apart.
        verdict = "collection-error" if junit["collection_errors"] else "session-interrupted"
    else:
        verdict = "no-tests-collected"

    if verdict == "unknown-exit":
        runner_exit = child_exit if child_exit > 0 else 128 - child_exit
    else:
        runner_exit = RUNNER_EXIT_BY_VERDICT[verdict]
    if (runner_exit == 0) != (verdict == "completed-clean"):
        raise AssertionError(f"exit mapping broke the clean-only-zero law: {verdict} -> "
                             f"{runner_exit}")

    failed_nodes: dict[str, Any] | None = None
    if junit is not None:
        failed_nodes = {
            node: {
                "outcome": "failure" if entry["failure"] else "error",
                "first_error": entry["first_error"],
                "first_error_lines": entry["first_error_lines"],
                "first_error_truncated": entry["first_error_lines"] > FIRST_ERROR_LINES,
                "timing": timing_failed.get(node),
            }
            for node, entry in sorted(junit["nodes"].items())
            if entry["failure"] or entry["error"]
        }
    return {
        "verdict": verdict,
        "runner_exit": runner_exit,
        "exit_code": child_exit,
        "complete": complete,
        "watchdog": watchdog,
        "timing_summary": {
            "complete": timing["complete"] if timing else None,
            "exitstatus": timing["exitstatus"] if timing else None,
            "session": timing["session"] if timing else None,
            "outcome_totals": totals,
        },
        "failed_nodes": failed_nodes,
        "fail_counts": {
            "failed_nodes_recorded": len(failed_nodes) if failed_nodes is not None else None,
            "junit_failure_elements": junit["failure_elements"] if junit else None,
            "junit_error_elements": junit["error_elements"] if junit else None,
            "junit_testcases": junit["testcases"] if junit else None,
            "junit_unanchored_nodeids": junit["unanchored_count"] if junit else None,
            "timing_failed": totals["failed"] if totals else None,
            "timing_errors": totals["errors"] if totals else None,
        },
        "collection_errors": junit["collection_errors"] if junit else None,
        "coverage": coverage,
        "evidence": evidence,
        "evidence_problems": problems,
        "inconsistencies": inconsistencies,
    }


def _harvest_crash(child_exit: int, exc: BaseException) -> dict[str, Any]:
    """The receipt must exist whatever the harvest hit: an unexpected error is
    itself a harvest failure, recorded with its traceback, never a crash."""

    return {
        "verdict": "harvest-failed",
        "runner_exit": EXIT_HARVEST_FAILED,
        "exit_code": child_exit,
        "complete": False,
        "watchdog": None,
        "timing_summary": {"complete": None, "exitstatus": None, "session": None,
                           "outcome_totals": None},
        "failed_nodes": None,
        "fail_counts": {"failed_nodes_recorded": None},
        "collection_errors": None,
        "coverage": None,
        "evidence": None,
        "evidence_problems": [f"harvest crashed: {type(exc).__name__}: {exc}"],
        "harvest_traceback": traceback.format_exception(exc)[-6:],
        "inconsistencies": [],
    }


def execute_run(repo: Path, run_dir: Path, profile_root: Path, python: str,
                selection: dict[str, Any], *, phase_timeout: float,
                wall_budget: float | None, node_budgets: bool,
                extra_note: str | None) -> dict[str, Any]:
    """Spawn the CI shard command and NEVER soften its exit. Raw output goes to
    unmodified files; the verdict comes from ``harvest`` -- the child's exit
    code alone never earns a completed verdict."""

    run_dir.mkdir(parents=True, exist_ok=True)
    watchdog_dir = run_dir / "watchdog"
    timing_path = run_dir / "timing.json"
    junit_path = run_dir / "junit.xml"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    home = profile_root / f"home-{stamp}-{os.getpid()}"
    basetemp = profile_root / f"basetemp-{stamp}-{os.getpid()}"
    home.mkdir(parents=True, exist_ok=True)
    basetemp.mkdir(parents=True, exist_ok=True)
    plugin_dir = run_dir / "_ledger_plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin_path = plugin_dir / f"{NODE_LEDGER_MODULE}.py"
    plugin_path.write_text(NODE_LEDGER_SOURCE, encoding="utf-8")
    ledger_path = run_dir / NODE_LEDGER_FILE

    command = [python, str(Path("ops") / "pytest_watchdog.py"),
               "--phase-timeout", str(phase_timeout),
               "--output", str(watchdog_dir)]
    if node_budgets and (repo / "ops" / "shard_weights.json").exists():
        command += ["--node-budgets", str(Path("ops") / "shard_weights.json")]
    command += ["--", python, str(Path("ops") / "pytest_timing.py"),
                "--output", str(timing_path), "--",
                "-q", "--tb=short", "-p", "no:cacheprovider",
                "-p", NODE_LEDGER_MODULE,
                "--junitxml", str(junit_path), "--basetemp", str(basetemp),
                *selection["files"]]

    env = _clean_pytest_env(dict(os.environ))
    env["VOOL_HOME"] = str(home)
    env[NODE_LEDGER_ENV] = str(ledger_path)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(plugin_dir), env.get("PYTHONPATH")]))
    started = time.monotonic()
    with (run_dir / "run-stdout.txt").open("wb") as out_handle, \
            (run_dir / "run-stderr.txt").open("wb") as err_handle:
        process = subprocess.Popen(
            command, cwd=str(repo), env=env, stdout=out_handle, stderr=err_handle,
        )
        interruption = None
        while process.poll() is None:
            if wall_budget is not None and time.monotonic() - started > wall_budget:
                if process.poll() is not None:
                    break
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

    try:
        harvested = harvest(child_exit=exit_code, runner_interrupted=interruption is not None,
                            run_dir=run_dir, selection_files=selection["files"])
    except Exception as exc:  # converted into a harvest-failed receipt
        harvested = _harvest_crash(exit_code, exc)

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        **harvested,
        "runner_interruption": interruption,
        "child_pid": process.pid,
        "command": command,
        "argv_deltas_vs_ci": [
            f"--junitxml {junit_path} (evidence sink: per-node outcomes + first error text)",
            f"--basetemp {basetemp} (scratch isolation; CI relies on the runner's tmp)",
            f"-p {NODE_LEDGER_MODULE} (evidence sink: collected/started node ids and per-phase "
            f"outcomes to {NODE_LEDGER_FILE}; selects nothing; module sha256 "
            f"{sha256_file(plugin_path)})",
            "xvfb-run wrapper absent on this platform (recorded in facilities)",
        ],
        "environment_note": extra_note,
        "wall_seconds": wall_seconds,
        "repo_root": str(repo),
        "vool_home": str(home),
        "basetemp": str(basetemp),
    }
    (run_dir / "RESULT.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Dynamic content that differs between two runs of the same failure: ISO
#: timestamps, UUIDs, long hex ids, object addresses. Paths are normalised
#: separately (each receipt's own roots first, then any absolute directory).
_DYNAMIC_PATTERNS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
                r"(?:Z|[+-]\d{2}:?\d{2})?"), "<timestamp>"),
    (re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9A-Za-z])"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "0x<addr>"),
    (re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{12,}"
                r"(?![0-9A-Za-z])"), "<hex>"),
)
_ABSOLUTE_DIR = re.compile(r"(?<![\w.~])/(?:[^\s/:'\"()<>]+/)+")


def normalise_first_error(text: str, roots: list[str]) -> str:
    """Strip run-specific content so two runs' first errors can be compared
    for what the failure SAYS. The raw strings stay in the comparison."""

    for root in sorted((r for r in roots if r), key=len, reverse=True):
        text = text.replace(root.rstrip("/") + "/", "<root>/")
    text = _ABSOLUTE_DIR.sub("<abs>/", text)
    for pattern, replacement in _DYNAMIC_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def receipt_problems(receipt: Any, role: str) -> list[str]:
    """Why one RESULT.json cannot be compared. Empty means it is an executed,
    complete, internally consistent receipt of a completed run."""

    if not isinstance(receipt, dict) or not receipt:
        return [f"{role}: empty or non-object receipt"]
    problems: list[str] = []
    verdict = receipt.get("verdict")
    if receipt.get("schema") != SCHEMA:
        problems.append(f"{role}: schema {receipt.get('schema')!r} != {SCHEMA}")
    if receipt.get("executed") is not True:
        problems.append(f"{role}: never executed (executed={receipt.get('executed')!r}, "
                        f"verdict {verdict!r})")
    if verdict not in COMPLETED_VERDICTS:
        problems.append(f"{role}: verdict {verdict!r} is not a completed run")
    if receipt.get("complete") is not True:
        problems.append(f"{role}: complete={receipt.get('complete')!r}")
    inconsistencies = receipt.get("inconsistencies")
    if not isinstance(inconsistencies, list) or inconsistencies:
        problems.append(f"{role}: harvest inconsistencies {inconsistencies!r}")
    evidence_problems = receipt.get("evidence_problems", [])
    if not isinstance(evidence_problems, list) or evidence_problems:
        problems.append(f"{role}: evidence problems {evidence_problems!r}")
    expected_exit = {"completed-clean": 0, "completed-with-failures": 1}.get(verdict)
    if expected_exit is not None and receipt.get("exit_code") != expected_exit:
        problems.append(f"{role}: exit_code {receipt.get('exit_code')!r} does not match "
                        f"verdict {verdict}")
    watchdog = receipt.get("watchdog")
    if (not isinstance(watchdog, dict) or watchdog.get("complete") is not True
            or watchdog.get("timed_out") is not False
            or watchdog.get("interrupted") is not False):
        problems.append(f"{role}: watchdog evidence is not a complete, uninterrupted run")
    timing = receipt.get("timing_summary")
    timing = timing if isinstance(timing, dict) else {}
    if timing.get("complete") is not True:
        problems.append(f"{role}: timing manifest complete={timing.get('complete')!r}")
    if timing.get("exitstatus") != receipt.get("exit_code"):
        problems.append(f"{role}: timing exitstatus {timing.get('exitstatus')!r} != "
                        f"exit_code {receipt.get('exit_code')!r}")
    totals = timing.get("outcome_totals")
    totals_ok = isinstance(totals, dict) and all(
        _is_int(totals.get(key)) for key in ("passed", "failed", "errors", "skipped"))
    if not totals_ok:
        problems.append(f"{role}: outcome totals missing or malformed (unknown)")
    selection = receipt.get("selection")
    sha = selection.get("sha256") if isinstance(selection, dict) else None
    if not isinstance(sha, str) or not _HEX64.match(sha):
        problems.append(f"{role}: selection sha256 missing or malformed ({sha!r})")
    failed = receipt.get("failed_nodes")
    if not isinstance(failed, dict):
        problems.append(f"{role}: failed_nodes missing (unknown)")
    elif totals_ok:
        timed = sum(1 for entry in failed.values()
                    if isinstance(entry, dict) and entry.get("timing"))
        if timed != totals["failed"]:
            problems.append(f"{role}: {timed} failed node(s) carry a timing record but the "
                            f"timing manifest counted {totals['failed']} failure(s)")
        if len(failed) - timed > totals["errors"]:
            problems.append(f"{role}: {len(failed) - timed} error node(s) exceed the timing "
                            f"error total {totals['errors']}")
        if verdict == "completed-clean" and (failed or totals["failed"] or totals["errors"]):
            problems.append(f"{role}: completed-clean with recorded failures")
        if verdict == "completed-with-failures" and not failed:
            problems.append(f"{role}: completed-with-failures with no failed node")
    problems.extend(_coverage_problems(receipt.get("coverage"), totals if totals_ok else None,
                                       role))
    return problems


def _coverage_problems(coverage: Any, totals: dict[str, Any] | None, role: str) -> list[str]:
    """A completed label is not trusted on its own: the receipt must carry
    reconciled case coverage whose numbers agree with themselves and with the
    receipt's outcome totals."""

    if not isinstance(coverage, dict):
        return [f"{role}: no case-coverage evidence (a receipt from before coverage was "
                "recorded, or an aborted harvest); it cannot be compared"]
    problems: list[str] = []
    if coverage.get("reconciled") is not True or coverage.get("reasons"):
        problems.append(f"{role}: case coverage not reconciled: {coverage.get('reasons')!r}")
    junit, timing = coverage.get("junit"), coverage.get("timing")
    shaped = all(isinstance(side, dict) and all(_is_int(side.get(key)) for key in COVERAGE_KEYS)
                 for side in (junit, timing))
    if not shaped:
        problems.append(f"{role}: case-coverage counts missing or malformed")
        return problems
    if junit != timing:
        problems.append(f"{role}: junit case coverage {junit} != timing {timing}")
    if coverage.get("collected") != timing["nodes"]:
        problems.append(f"{role}: {coverage.get('collected')!r} node(s) collected but "
                        f"{timing['nodes']} started")
    if coverage.get("mismatched_file_count") or coverage.get("files_outside_selection"):
        problems.append(f"{role}: per-file coverage mismatches recorded")
    identity = coverage.get("identity")
    if not isinstance(identity, dict) or identity.get("verified") is not True:
        problems.append(f"{role}: no verified node-identity evidence (a receipt from before "
                        "the node ledger existed cannot be re-harvested into one; re-run it)")
    elif (identity.get("reasons") or identity.get("missing_count") or identity.get("unexpected_count")
          or identity.get("mismatch_count")
          or not (identity.get("started") == identity.get("collected")
                  == identity.get("junit_nodes") == timing["nodes"])):
        problems.append(f"{role}: node-identity evidence does not agree with itself or with "
                        f"the case coverage: {identity!r}")
    if totals is not None:
        expected = {"pass": totals.get("passed", 0) + totals.get("xpassed", 0),
                    "failure": totals["failed"], "error": totals["errors"],
                    "skip": totals["skipped"], "xfail": totals.get("xfailed", 0)}
        if any(timing[key] != value for key, value in expected.items()):
            problems.append(f"{role}: case coverage {timing} disagrees with outcome totals "
                            f"{totals}")
    return problems


def compare_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable baseline/candidate diff of failed nodes AND first
    errors. Both receipts must be executed, complete and consistent runs of a
    byte-identical selection (same sha256); otherwise the comparison is
    REFUSED: ``problems`` says why and no node diff is presented. Heads differ
    by design and are both recorded. Environment deltas are listed as FACTS --
    nothing here exonerates a failure or declares 'no regression'."""

    problems = receipt_problems(baseline, "baseline") + receipt_problems(candidate, "candidate")
    baseline = baseline if isinstance(baseline, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    base_sel = baseline.get("selection") if isinstance(baseline.get("selection"), dict) else {}
    cand_sel = candidate.get("selection") if isinstance(candidate.get("selection"), dict) else {}
    if base_sel.get("sha256") != cand_sel.get("sha256"):
        problems.append(
            f"selection mismatch: baseline {base_sel.get('sha256')} vs candidate "
            f"{cand_sel.get('sha256')} -- different selections are never compared")
    comparable = not problems

    def summary(run: dict[str, Any], sel: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": run.get("label"),
            "head": (run.get("git") or {}).get("head"),
            "verdict": run.get("verdict"),
            "exit_code": run.get("exit_code"),
            "complete": run.get("complete"),
            "executed": run.get("executed"),
            "outcome_totals": (run.get("timing_summary") or {}).get("outcome_totals"),
            "selection_sha256": sel.get("sha256"),
        }

    comparison: dict[str, Any] = {
        "schema": SCHEMA + ".compare",
        "comparable": comparable,
        "problems": problems,
        "baseline": summary(baseline, base_sel),
        "candidate": summary(candidate, cand_sel),
        "failed_in_both": [],
        "failed_only_in_baseline": [],
        "failed_only_in_candidate": [],
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
    if not comparable:
        comparison["node_diff_withheld"] = (
            "receipts are not comparable (see problems); no failed-node diff is "
            "presented, so nothing here can read as a fix or a regression")
        return comparison

    base_failed = set(baseline["failed_nodes"])
    cand_failed = set(candidate["failed_nodes"])

    def first_error(run: dict[str, Any], node: str) -> str:
        return (run["failed_nodes"].get(node) or {}).get("first_error", "")

    def truncated(run: dict[str, Any], node: str) -> bool:
        entry = run["failed_nodes"].get(node) or {}
        if "first_error_truncated" in entry:
            return bool(entry["first_error_truncated"])
        # Receipts from before the flag existed: an excerpt AT the cap may have
        # been cut, so equality beyond it is unknown.
        return len(entry.get("first_error", "").splitlines()) >= FIRST_ERROR_LINES

    def roots(run: dict[str, Any]) -> list[str]:
        return [str(run.get(key) or "") for key in ("repo_root", "vool_home", "basetemp")]

    both = []
    for node in sorted(base_failed & cand_failed):
        base_text, cand_text = first_error(baseline, node), first_error(candidate, node)
        both.append({
            "nodeid": node,
            "baseline_first_error": base_text,
            "candidate_first_error": cand_text,
            "first_error_equal_raw": base_text == cand_text,
            "first_error_equal_normalised": (
                normalise_first_error(base_text, roots(baseline))
                == normalise_first_error(cand_text, roots(candidate))),
            "excerpt_truncated": truncated(baseline, node) or truncated(candidate, node),
        })
    comparison["failed_in_both"] = both
    comparison["failed_only_in_baseline"] = [
        {"nodeid": node, "first_error": first_error(baseline, node)}
        for node in sorted(base_failed - cand_failed)]
    comparison["failed_only_in_candidate"] = [
        {"nodeid": node, "first_error": first_error(candidate, node)}
        for node in sorted(cand_failed - base_failed)]
    comparison["first_error_summary"] = {
        "failed_in_both": len(both),
        "raw_differ": sum(1 for entry in both if not entry["first_error_equal_raw"]),
        "normalised_differ": sum(1 for entry in both
                                 if not entry["first_error_equal_normalised"]),
        "excerpt_truncated": sum(1 for entry in both if entry["excerpt_truncated"]),
        "normalisation": "receipt roots (repo, VOOL_HOME, basetemp), absolute directories, "
                         "ISO timestamps, UUIDs, 0x addresses and hex ids of 12+ chars are "
                         "replaced; the raw strings are kept above",
    }
    return comparison


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _refuse(run_dir: Path, *, label: str, problems: list[str],
            exit_code: int = EXIT_REFUSED_PREFLIGHT, receipt: Path | None = None,
            **facts: Any) -> int:
    """Write a refused receipt -- it never claims execution -- and return the
    runner exit. Every refusal leaves a receipt."""

    payload = {"schema": SCHEMA, "verdict": "refused", "executed": False,
               "label": label, "refusals": problems, "runner_exit": exit_code, **facts}
    _write_json(receipt or (run_dir / "RESULT.json"), payload)
    print(f"REFUSED ({len(problems)} problem(s)); no execution claimed", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return exit_code


def command_run(args: argparse.Namespace) -> int:
    runner_started = time.monotonic()
    repo = args.repo.resolve()
    run_dir = args.artifacts / args.label
    run_dir.mkdir(parents=True, exist_ok=True)
    stale = [str(relative) for relative in _EVIDENCE_FILES if (run_dir / relative).exists()]
    if stale:
        # A label's evidence belongs to ONE run: reusing it would let a run that
        # aborts before writing its own timing/junit harvest the previous run's
        # files. The earlier receipt is preserved; this refusal gets its own file.
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        return _refuse(run_dir, label=args.label, receipt=run_dir / f"REFUSED-{stamp}-{os.getpid()}.json",
                       problems=[f"run directory already holds evidence from an earlier run "
                                 f"({', '.join(stale)}); use a new --label"])

    budget = args.wall_budget
    spent = 0.0  # wall-budget seconds used by collection and resolution
    timings: dict[str, Any] = {}

    def bounded(cap: float) -> float:
        return cap if budget is None else min(cap, max(budget - spent, 0.0))

    selection: dict[str, Any]
    collection: dict[str, Any] = {}
    resolution: dict[str, Any] = {}
    if args.selection_explicit:
        selection = _load_explicit_selection(Path(args.selection_explicit), args.selection_label)
    else:
        collection = canonical_collection(repo, run_dir / "collect", args.python,
                                          profile_root=args.profiles,
                                          timeout=bounded(args.collect_timeout))
        spent += collection["collection_seconds"]
        timings["collection_seconds"] = collection["collection_seconds"]
        if collection["collection_timed_out"]:
            return _refuse(run_dir, label=args.label, collection=collection, timings=timings,
                           problems=[f"canonical collection timed out after "
                                     f"{collection['collection_timeout_seconds']}s "
                                     f"(pid {collection['collection_pid']}, its group stopped)"])
        if collection["collection_command_exit"] != 0:
            return _refuse(run_dir, label=args.label, collection=collection, timings=timings,
                           problems=[f"canonical collection exited "
                                     f"{collection['collection_command_exit']}: a partial "
                                     "collection is not the canonical selection (pin an "
                                     "explicit file list to run a historical selection)"])
        linux_files = _read_file_list(run_dir / "collect" / "linux-collected.txt")
        if args.shard is None:
            selection = build_selection(
                source="canonical-shard",
                label=args.selection_label,
                files=linux_files,
                provenance="live canonical collection of THIS machine; NO shard "
                           "partition (whole linux selection)",
            )
        else:
            resolution = resolve_canonical_shard(
                repo, run_dir / "collect", args.python,
                shard=args.shard, shards=args.shards, timeout=bounded(RESOLVER_TIMEOUT))
            spent += resolution["resolver_seconds"]
            timings["resolver_seconds"] = resolution["resolver_seconds"]
            if resolution["resolver_timed_out"] or resolution["resolver_command_exit"] != 0:
                reason = ("timed out" if resolution["resolver_timed_out"]
                          else f"exited {resolution['resolver_command_exit']}")
                return _refuse(run_dir, label=args.label, collection=collection,
                               resolution=resolution, timings=timings,
                               problems=[f"shard resolver {reason}; no execution claimed"])
            selection = build_selection(
                source="canonical-shard",
                label=args.selection_label,
                files=resolution["files"],
                provenance=(
                    f"canonical collection ({collection['linux_files']} linux files, "
                    f"collection exit 0) partitioned by ops/shard_resolver.py with "
                    f"--platform linux (CI partition); resolver exit 0"
                ),
            )
    if args.prefix is not None:
        if not 1 <= args.prefix <= len(selection["files"]):
            return _refuse(run_dir, label=args.label, selection=selection,
                           collection=collection or None, resolution=resolution or None,
                           problems=[f"--prefix {args.prefix} outside "
                                     f"1..{len(selection['files'])}"])
        selection = dict(selection)
        selection["files"] = selection["files"][:args.prefix]
        selection["file_count"] = args.prefix
        selection["sha256"] = _sha256_bytes(
            ("\n".join(selection["files"]) + "\n").encode("utf-8"))
        selection["label"] = f"{selection['label']}[prefix-{args.prefix}]"
        selection["provenance"] += f"; bounded to first {args.prefix} files IN ORDER"

    _write_json(run_dir / "selection.json", selection)
    (run_dir / "selection.txt").write_text(
        "\n".join(selection["files"]) + "\n", encoding="utf-8")

    identity_started = time.monotonic()
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
    _write_json(run_dir / "fingerprints.json", fingerprints)
    timings["identity_probe_seconds"] = round(time.monotonic() - identity_started, 3)

    execution_budget = None if budget is None else round(budget - spent, 3)
    if not problems and execution_budget is not None and execution_budget <= 0:
        problems.append(f"wall budget {budget}s exhausted by collection/resolution "
                        f"({spent:.3f}s) before execution")
    if problems:
        timings["runner_wall_seconds"] = round(time.monotonic() - runner_started, 3)
        return _refuse(
            run_dir, label=args.label, problems=problems, selection=selection,
            git=facts.get("git"), environment=environment,
            collection=collection or None, resolution=resolution or None, timings=timings,
            exit_code=(EXIT_REFUSED_PIN if any("pinned" in p or "changed since pin" in p
                                               for p in problems) else EXIT_REFUSED_PREFLIGHT))

    payload = execute_run(
        repo, run_dir, Path(args.profiles), args.python, selection,
        phase_timeout=args.phase_timeout, wall_budget=execution_budget,
        node_budgets=not args.no_node_budgets, extra_note=args.note,
    )
    timings["execution_wall_seconds"] = payload["wall_seconds"]
    timings["runner_wall_seconds"] = round(time.monotonic() - runner_started, 3)
    payload.update({
        "label": args.label,
        "selection": selection,
        "collection": collection or None,
        "resolution": resolution or None,
        "git": facts.get("git"),
        "environment": environment,
        "facilities": facts.get("facilities"),
        "executed": True,
        "timings": timings,
        "wall_budget": {"declared_seconds": budget,
                        "spent_before_execution_seconds": round(spent, 3),
                        "execution_budget_seconds": execution_budget,
                        "covers": "canonical collection, shard resolution and execution"},
    })
    payload["head_identity"] = {**fingerprints["head_identity"],
                                "repo_head": facts.get("git", {}).get("head"),
                                "repo_tree": facts.get("git", {}).get("tree")}
    if args.selection_explicit:
        payload["head_identity"]["selection_input_sha256"] = fingerprints[
            "selection_input_sha256"]
    _write_json(run_dir / "RESULT.json", payload)
    print(f"label={args.label} verdict={payload['verdict']} exit={payload['exit_code']} "
          f"runner_exit={payload['runner_exit']} complete={payload['complete']} "
          f"failed_nodes={payload['fail_counts']['failed_nodes_recorded']}")
    for problem in payload["evidence_problems"] + payload["inconsistencies"]:
        print(f"  - {problem}", file=sys.stderr)
    return payload["runner_exit"]


def command_harvest(args: argparse.Namespace) -> int:
    """Re-read an archived run directory's evidence with THIS runner's harvest
    rules. Nothing is executed; the source RESULT.json is read, never written.
    The re-harvested receipt goes to ``--output``."""

    run_dir = Path(args.run_dir).resolve()
    source_path = run_dir / "RESULT.json"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = _load_json_object(source_path, "source receipt")
    except EvidenceError as exc:
        _write_json(output, {"schema": SCHEMA, "verdict": "harvest-failed",
                             "runner_exit": EXIT_HARVEST_FAILED,
                             "evidence_problems": [str(exc)], "executed": None})
        print(f"HARVEST FAILED: {exc}", file=sys.stderr)
        return EXIT_HARVEST_FAILED
    problems: list[str] = []
    child_exit = source.get("exit_code")
    selection = source.get("selection")
    if source.get("executed") is not True:
        problems.append(f"source receipt never executed (verdict {source.get('verdict')!r}); "
                        "there is no run to harvest")
    if not _is_int(child_exit):
        problems.append(f"source receipt has no child exit code ({child_exit!r})")
    if not isinstance(selection, dict) or not isinstance(selection.get("files"), list):
        problems.append("source receipt has no selection file list")
    else:
        recomputed = _sha256_bytes(("\n".join(selection["files"]) + "\n").encode("utf-8"))
        if recomputed != selection.get("sha256"):
            problems.append(f"selection sha256 {selection.get('sha256')} does not match its own "
                            f"file list ({recomputed})")
        selection_txt = run_dir / "selection.txt"
        if not selection_txt.is_file() or sha256_file(selection_txt) != recomputed:
            problems.append("selection.txt in the run directory is missing or differs from "
                            "the receipt's selection")
    if problems:
        _write_json(output, {"schema": SCHEMA, "verdict": "harvest-failed",
                             "runner_exit": EXIT_HARVEST_FAILED, "evidence_problems": problems,
                             "executed": source.get("executed")})
        for problem in problems:
            print(f"HARVEST FAILED: {problem}", file=sys.stderr)
        return EXIT_HARVEST_FAILED
    try:
        harvested = harvest(
            child_exit=child_exit,
            runner_interrupted=(source.get("runner_interruption") is not None
                                or source.get("verdict") == "interrupted-by-wall-budget"),
            run_dir=run_dir, selection_files=selection["files"])
    except Exception as exc:  # converted into a harvest-failed receipt
        harvested = _harvest_crash(child_exit, exc)
    receipt = {key: value for key, value in source.items() if key not in harvested}
    receipt.update(harvested)
    receipt["schema"] = SCHEMA
    receipt["reharvest"] = {
        "source_receipt": str(source_path),
        "source_receipt_sha256": sha256_file(source_path),
        "source_verdict": source.get("verdict"),
        "source_runner_exit": source.get("runner_exit"),
        "harvested_with_runner_sha256": sha256_file(Path(__file__).resolve()),
        "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "evidence re-read from the archived run directory; nothing was executed",
    }
    _write_json(output, receipt)
    print(f"reharvest {run_dir.name}: verdict={receipt['verdict']} exit={receipt['exit_code']} "
          f"runner_exit={receipt['runner_exit']} complete={receipt['complete']} "
          f"failed_nodes={receipt['fail_counts']['failed_nodes_recorded']}")
    for problem in receipt["evidence_problems"] + receipt["inconsistencies"]:
        print(f"  - {problem}", file=sys.stderr)
    return receipt["runner_exit"]


def command_collect(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    collection = canonical_collection(repo, args.artifacts, args.python,
                                      profile_root=args.profiles,
                                      timeout=args.collect_timeout)
    manifest = canonical_manifest(repo, args.artifacts, args.python,
                                  profile_root=args.profiles,
                                  timeout=args.collect_timeout)
    payload = {"schema": SCHEMA, "collection": collection, "manifest": manifest,
               "git": git_info(repo), "environment": environment_fingerprint(args.python)}
    _write_json(args.artifacts / "COLLECTION.json", payload)
    print(json.dumps({"collection": collection, "manifest": manifest}, indent=2))
    return 0 if (collection["collection_command_exit"] == 0
                 and manifest["manifest_command_exit"] == 0) else 1


def _read_receipt(path: str, role: str) -> tuple[Any, list[str]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [f"{role}: receipt unreadable ({type(exc).__name__}: {exc})"]


def command_compare(args: argparse.Namespace) -> int:
    baseline, base_problems = _read_receipt(args.baseline, "baseline")
    candidate, cand_problems = _read_receipt(args.candidate, "candidate")
    comparison = compare_runs(baseline, candidate)
    if base_problems or cand_problems:
        comparison["problems"] = base_problems + cand_problems + comparison["problems"]
        comparison["comparable"] = False
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    _write_json(Path(args.output), comparison)
    summary = {
        "comparable": comparison["comparable"],
        "failed_in_both": len(comparison["failed_in_both"]),
        "failed_only_in_baseline": len(comparison["failed_only_in_baseline"]),
        "failed_only_in_candidate": len(comparison["failed_only_in_candidate"]),
        "first_error_summary": comparison.get("first_error_summary"),
        "problems": comparison["problems"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if comparison["comparable"] else 1


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
    run.add_argument("--collect-timeout", type=float, default=DEFAULT_COLLECT_TIMEOUT,
                     help="bound for the canonical collection; on expiry the run is "
                          "refused with a receipt")
    run.add_argument("--wall-budget", type=float, default=None,
                     help="overall wall budget covering canonical collection, shard "
                          "resolution and execution; on exceed the run is refused "
                          "(before execution) or interrupted (only this tool's own "
                          "recorded child is signalled)")
    run.add_argument("--no-node-budgets", action="store_true")
    run.add_argument("--require-head")
    run.add_argument("--require-selection-sha256")
    run.add_argument("--require-file-hashes", help="file_hashes JSON from a prior "
                    "run's fingerprints.json (changed-source detection)")
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--note")
    run.set_defaults(func=command_run)

    collect = sub.add_parser("collect", help="canonical collection + manifest only")
    collect.add_argument("--collect-timeout", type=float, default=DEFAULT_COLLECT_TIMEOUT,
                         help="bound for the collection and for the manifest step")
    collect.set_defaults(func=command_collect)

    compare = sub.add_parser("compare", help="baseline/candidate failed-node diff")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--output", required=True)
    compare.set_defaults(func=command_compare)

    reharvest = sub.add_parser("harvest", help="re-harvest an archived run directory's "
                                               "evidence without executing anything")
    reharvest.add_argument("--run-dir", required=True,
                           help="archived run directory (holds RESULT.json, timing.json, "
                                "junit.xml, watchdog/result.json)")
    reharvest.add_argument("--output", required=True,
                           help="where the re-harvested receipt is written")
    reharvest.set_defaults(func=command_harvest)
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

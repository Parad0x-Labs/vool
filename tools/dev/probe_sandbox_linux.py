#!/usr/bin/env python3
"""Diagnose why sandbox/job_runner.py cannot execute steps on this host.

Exercises the REAL runner code -- sandbox.job_runner.JobRunner and its private
backend builders -- never a reimplementation, so whatever this prints is what
the product would do. Zero network: the only programs it runs are the backend
binaries themselves wrapping ``true``/``echo``.

For each backend (bwrap, unshare, firejail on Linux; sandbox-exec on macOS) it
reports, verbatim:

  1. the PATH probe result (``shutil.which``) the runner performs;
  2. the EXACT probe command ``_backend_usable`` runs, and its captured
     exit code / stdout / stderr (the runner swallows these; a CI diagnosis
     needs the strings);
  3. the EXACT wrapped argv the runner would build for a trivial
     ``echo ok`` confined exec, and that argv's captured exit/stdout/stderr
     when executed with the runner's own sanitized child environment;
  4. the refusal string the runner would surface when no backend is usable
     (``KernelIsolationUnavailableError``'s message, from the real method).

Then it drives the full real entry point ``JobRunner.run(["echo", "ok"])``
against a disposable /tmp workspace and prints the ExecutionResult, plus a
write-denial check that shows whether the selected backend actually confines
writes outside the workspace (the difference between "runs" and "sandboxes").

Exit code 0 only if the real run() executed the command successfully.

Usage: python tools/dev/probe_sandbox_linux.py
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# sys.path was set up above, so these imports must follow it
from sandbox import job_runner
from sandbox.resource_limits import ExecutionPolicy

_BACKEND_TIMEOUT_SECONDS = 20


def _hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _show_completion(prefix: str, completed: subprocess.CompletedProcess | None) -> None:
    if completed is None:
        print(f"{prefix}: <not attempted on this platform>")
        return
    print(f"{prefix}: exit={completed.returncode}")
    print(f"{prefix}: stdout={completed.stdout!r}")
    print(f"{prefix}: stderr={completed.stderr!r}")


def _run_raw(argv: list[str]) -> subprocess.CompletedProcess | None:
    """Run a wrapped argv exactly as printed, capturing its own failure text."""
    if argv is None:
        return None
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=_BACKEND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            argv,
            exc.returncode or 124,
            _safe_text(exc.stdout),
            _safe_text(exc.stderr) or f"<timed out after {_BACKEND_TIMEOUT_SECONDS}s>",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"<spawn failed: {exc}>")


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _probe_backend(runner: job_runner.JobRunner, name: str, cache_key: str, probe_argv: list[str] | None) -> bool:
    """The runner's own availability probe, plus the strings it discards."""
    path = shutil.which(name)
    print(f"\n-- backend '{name}'")
    print(f"path probe (shutil.which): {path!r}")
    if probe_argv is None:
        print("runner gate: not applicable on this platform (prefix builder returned None)")
        return False
    print(f"_backend_usable probe argv: {probe_argv}")
    job_runner.reset_isolation_backend_probe_cache()
    usable = job_runner._backend_usable(cache_key, probe_argv)
    print(f"_backend_usable verdict: {usable}")
    _show_completion("  raw probe", _run_raw(probe_argv))
    return usable


def _linux_userns_sysctls() -> None:
    """Read-only kernel facts that decide whether bwrap/unshare can work here."""
    candidates = [
        "/proc/sys/kernel/unprivileged_userns_clone",
        "/proc/sys/user/max_user_namespaces",
        "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
        "/sys/module/apparmor/parameters/enabled",
    ]
    for cand in candidates:
        try:
            value = Path(cand).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        print(f"sysctl {cand} = {value}")


def main() -> int:
    print(f"probe host : {platform.platform()}")
    print(f"python     : {sys.version.split()[0]} ({sys.executable})")
    print(f"repo root  : {REPO_ROOT}")
    print(f"job_runner : {Path(job_runner.__file__).resolve()}")

    workspace = Path(tempfile.mkdtemp(prefix="vool-sandbox-probe-")).resolve()
    policy = ExecutionPolicy(workspace_root=workspace)
    runner = job_runner.JobRunner(policy)
    print(f"workspace  : {workspace} (disposable, removed on exit)")

    is_linux = sys.platform.startswith("linux")
    is_macos = sys.platform == "darwin"

    _hr("BACKEND AVAILABILITY -- the runner's own probes")
    bwrap = shutil.which("bwrap")
    if is_linux and bwrap:
        # deny_network=True: the default no-egress sandbox. deny_network=False:
        # what a network-trusted local test/lint run still wraps in.
        _probe_backend(
            runner,
            "bwrap",
            "bwrap:deny_network=True",
            [bwrap, "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
        )
        print(
            f"_backend_usable probe argv (deny_network=False): "
            f"{[bwrap, '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--', 'true']}"
        )
        job_runner.reset_isolation_backend_probe_cache()
        usable_plain = job_runner._backend_usable(
            "bwrap:deny_network=False",
            [bwrap, "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
        )
        print(f"_backend_usable verdict (deny_network=False): {usable_plain}")
        _show_completion(
            "  raw probe (deny_network=False)",
            _run_raw([bwrap, "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"]),
        )
    else:
        print(f"\n-- backend 'bwrap': {'not on PATH' if is_linux else 'Linux-only backend'}")

    unshare = shutil.which("unshare")
    if is_linux and unshare:
        _probe_backend(runner, "unshare", "unshare", [unshare, "-n", "--", "true"])
    else:
        print(f"\n-- backend 'unshare': {'not on PATH' if is_linux else 'Linux-only backend'}")

    firejail = shutil.which("firejail")
    if is_linux and firejail:
        _probe_backend(runner, "firejail", "firejail", [firejail, "--net=none", "--quiet", "--", "true"])
    else:
        print(f"\n-- backend 'firejail': {'not on PATH' if is_linux else 'Linux-only backend'}")

    sandbox_exec = shutil.which("sandbox-exec")
    if is_macos and sandbox_exec:
        _probe_backend(
            runner,
            "sandbox-exec",
            "sandbox-exec",
            [sandbox_exec, "-p", "(version 1)(allow default)(deny network*)", "--", "true"],
        )
    else:
        print(f"\n-- backend 'sandbox-exec': {'not on PATH' if is_macos else 'macOS-only backend'}")

    if is_linux:
        _hr("KERNEL NAMESPACE FACTS (read-only)")
        _linux_userns_sysctls()

    _hr("EXACT WRAPPED COMMAND for a trivial confined `echo ok`")
    roots = (policy.workspace_root, *policy.writable_roots)
    echo = ["echo", "ok"]
    builders = [
        ("_linux_bwrap_prefix (deny_network=True)", lambda: runner._linux_bwrap_prefix(echo, roots)),
        (
            "_linux_bwrap_prefix (deny_network=False)",
            lambda: runner._linux_bwrap_prefix(echo, roots, deny_network=False),
        ),
        ("_linux_unshare_prefix", lambda: runner._linux_unshare_prefix(echo)),
        ("_linux_firejail_prefix", lambda: runner._linux_firejail_prefix(echo)),
        ("_macos_sandbox_exec_prefix", lambda: runner._macos_sandbox_exec_prefix(echo, roots)),
    ]
    for label, build in builders:
        wrapped = build()
        print(f"\n-- {label}")
        if wrapped is None:
            print("returned None (backend unavailable or wrong platform -- runner skips it)")
            continue
        if label.startswith("_macos"):
            profile_preview = wrapped[2][:400] + ("..." if len(wrapped[2]) > 400 else "")
            print(
                f"wrapped argv (Seatbelt profile elided, {len(wrapped[2])} chars): {*wrapped[:2], '<profile>', *wrapped[3:]}"
            )
            print(f"profile preview: {profile_preview}")
        else:
            print(f"wrapped argv: {wrapped}")
        env = runner._child_environment(workspace)
        try:
            completed = subprocess.run(
                wrapped,
                capture_output=True,
                text=True,
                shell=False,
                check=False,
                timeout=_BACKEND_TIMEOUT_SECONDS,
                env=env,
                cwd=str(workspace),
            )
            _show_completion("  exec with the runner's own child env", completed)
        except subprocess.TimeoutExpired as exc:
            print(
                f"  exec with the runner's own child env: <timed out after {_BACKEND_TIMEOUT_SECONDS}s> partial={_safe_text(exc.stderr)!r}"
            )
        except OSError as exc:
            print(f"  exec with the runner's own child env: <spawn failed: {exc}>")

    selected = runner._kernel_network_isolation_prefix(echo, roots)
    print(f"\nselection order (bwrap -> unshare -> firejail -> sandbox-exec) resolves to: {selected!r}")

    _hr("REFUSAL STRING the runner surfaces when NO backend is usable")
    print(runner._no_kernel_isolation_message())

    _hr("FULL REAL PATH -- JobRunner.run(['echo', 'ok']) (guards, env, wrapper, wait)")
    verdict_ok = False
    try:
        result = runner.run(echo)
        print(f"ExecutionResult.returncode = {result.returncode}")
        print(f"ExecutionResult.stdout     = {result.stdout!r}")
        print(f"ExecutionResult.stderr     = {result.stderr!r}")
        verdict_ok = result.returncode == 0 and result.stdout.strip() == "ok"
    except job_runner.KernelIsolationUnavailableError as exc:
        print(f"KernelIsolationUnavailableError: {exc}")
    except Exception as exc:  # a diagnosis tool reports, never hides
        print(f"{type(exc).__name__}: {exc}")

    _hr("WRITE-DENIAL CHECK -- does the selected backend actually confine?")
    outside = workspace.parent / f"{workspace.name}-outside"
    outside.mkdir(exist_ok=True)
    try:
        probe_argv = ["/bin/sh", "-c", f"echo leaked > {outside / 'leak.txt'}"]
        result = runner.run(probe_argv)
        leaked = (outside / "leak.txt").exists()
        print(f"write outside workspace: exit={result.returncode} leaked={leaked}")
        print(f"stderr={result.stderr!r}")
        if leaked:
            print("!! CONFIRMED: the write ESCAPED the workspace -- backend runs but does not confine")
    except Exception as exc:
        print(f"write-denial check raised {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(outside, ignore_errors=True)

    _hr("VERDICT")
    print(f"JobRunner.run(['echo','ok']) executed successfully: {verdict_ok}")
    print(f"selected backend: {selected[0] if selected else 'NONE -- run() would refuse'}")

    shutil.rmtree(workspace, ignore_errors=True)
    print(f"cleaned disposable workspace {workspace}")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

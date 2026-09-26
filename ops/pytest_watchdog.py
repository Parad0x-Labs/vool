"""Bound stalled pytest phases without changing selection, ordering or verdicts.

A separate supervisor survives a stuck test, fixture, collection or interpreter
shutdown. Only its direct child can update progress; nested pytest processes
cannot hide an outer hang. Timeout is a red, incomplete run, never a skip.

Clock authority: the CHILD's progress payload identifies the phase and node,
but elapsed time is measured by the SUPERVISOR's own monotonic clock, from the
moment it last observed the payload CHANGE. Tests may legitimately simulate a
business clock by patching the shared stdlib ``time`` module (e.g. the usepod
price-wait suite) while the teardown progress hook fires, and the run that
produced artifact 10902318921 showed why child timestamps must never drive
deadlines: the child reported the fake ``at`` 1000.2 while its real clock read
2967.6 and it had already advanced to the next file -- the supervisor declared
a 600s "teardown stall" 0.3s into real work. Supervisor-owned observation
time also means a child-origin fake or future timestamp can neither trigger
nor extend any real deadline.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import math
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

_PROGRESS = "VOOL_CI_PROGRESS"
_OWNER = "VOOL_CI_PROGRESS_PID"
_STACK = "VOOL_CI_STACKS"
_stack_stream = None
#: Bound at import, before any test can patch the shared ``time`` module's
#: ``monotonic`` attribute (the usepod price-wait suites do exactly that).
#: The captured reference keeps the watchdog's own timestamps real even while
#: a test's business clock is simulated.
_MONOTONIC = time.monotonic


def _progress(phase: str, node: str = "") -> None:
    if os.environ.get(_OWNER) != str(os.getpid()):
        return
    path = Path(os.environ[_PROGRESS])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "phase": phase,
                                     "node": node, "at": _MONOTONIC()}))
    temporary.replace(path)


def pytest_configure(config):
    del config
    global _stack_stream
    if os.environ.get(_OWNER) == str(os.getpid()):
        _stack_stream = open(os.environ[_STACK], "a", encoding="utf-8")  # noqa: SIM115 -- must survive pytest shutdown
        faulthandler.register(signal.SIGUSR1, file=_stack_stream, all_threads=True)
        _progress("collection")


def pytest_collectstart(collector):
    _progress("collection", collector.nodeid)


def pytest_collection_finish(session):
    del session
    _progress("collection complete")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _progress("setup", item.nodeid)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    _progress("call", item.nodeid)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    del nextitem
    _progress("teardown", item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus
    _progress("session finish / process shutdown")


def _stop_group(process):
    # A test may leave a child alive after pytest exits: terminate the owned
    # process group, never other workers or unrelated runtime processes.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
    process.wait(timeout=5)


def _load_node_budgets(path):
    """Measured per-file durations (``ops/shard_weights.json``'s ``weights`` map, or a flat
    file→seconds map). A module-scoped fixture can legitimately spend the file's WHOLE measured
    duration inside one opaque setup phase — the corpus replay module measures ~44 minutes —
    and a flat phase bound would kill known-slow work as a stall. Budgets come only from a
    completed instrumented run, so an unmeasured or new file keeps the base bound."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    candidate = payload.get("weights") if isinstance(payload.get("weights"), dict) else payload
    return {str(key): float(value) for key, value in candidate.items()
            if isinstance(value, (int, float)) and float(value) > 0}


def _effective_phase_timeout(base, budgets, margin, node):
    file_id = str(node or "").split("::", 1)[0]
    measured = budgets.get(file_id) if budgets else None
    if not measured:
        return base
    return max(base, math.ceil(measured * margin))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--child"]:
        os.environ[_OWNER] = str(os.getpid())
        os.execvpe(args[1], args[1:], os.environ)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-timeout", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node-budgets", type=Path, default=None,
                        help="measured per-file durations; a node's file may spend up to "
                             "measured*margin in one phase before the bound applies")
    parser.add_argument("--budget-margin", type=float, default=1.5)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(args)
    if os.name != "posix":
        parser.error("the CI watchdog requires POSIX process groups")
    if not math.isfinite(options.phase_timeout) or options.phase_timeout <= 0:
        parser.error("phase timeout must be positive and finite")
    if not math.isfinite(options.budget_margin) or options.budget_margin < 1:
        parser.error("budget margin must be finite and >= 1")
    command = options.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required")
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    budgets = _load_node_budgets(options.node_budgets) if options.node_budgets else {}
    progress = output / "progress.json"
    stacks = output / "stacks.log"
    # Use an exclusive directory per invocation; stale success must not survive.
    for path in (progress, stacks, output / "result.json"):
        path.unlink(missing_ok=True)
    env = os.environ.copy()
    env[_PROGRESS], env[_STACK] = str(progress), str(stacks)
    plugins = [p for p in env.get("PYTEST_PLUGINS", "").split(",") if p]
    if "ops.pytest_watchdog" not in plugins:
        plugins.append("ops.pytest_watchdog")
    env["PYTEST_PLUGINS"] = ",".join(plugins)
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                "--child", *command], env=env, start_new_session=True)
    started = _MONOTONIC()
    last = {"phase": "startup / plugin loading", "node": "", "at": started}
    # Elapsed time is measured HERE, on the supervisor's own monotonic clock,
    # from the last observed payload CHANGE -- never from the child-reported
    # ``at`` field, which a test's business-clock patch of the shared time
    # module can falsify while the watchdog's teardown hook fires (a fake
    # ``at`` behind the real clock manufactured a "600s teardown stall" in a
    # 0.3s window; artifact 10902318921). A payload change (new phase or node)
    # is the same forward-progress signal the old ``at`` update carried, so
    # genuine phase budgets -- including measured per-file ones -- keep their
    # exact semantics; a fake or future child timestamp can no longer trip a
    # false stall nor extend any real deadline.
    last_payload = None
    last_change = started
    timed_out = False
    interrupted = False

    def cancel(signum, frame):
        del signum, frame
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, cancel)
    try:
        while process.poll() is None:
            try:
                current = json.loads(progress.read_text())
                if current.get("pid") == process.pid:
                    payload = (current.get("phase"), current.get("node"))
                    if payload != last_payload:
                        last_payload = payload
                        last_change = _MONOTONIC()
                    last = current
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            effective = _effective_phase_timeout(options.phase_timeout, budgets,
                                                 options.budget_margin, last.get("node"))
            elapsed = _MONOTONIC() - last_change
            if elapsed > effective:
                timed_out = True
                print(f"CI STALL: {last['phase']} {last['node']} exceeded "
                      f"{effective:g}s; refusing an incomplete run", flush=True)
                if stacks.exists():
                    try:
                        os.kill(process.pid, signal.SIGUSR1)
                        time.sleep(0.2)
                        print(stacks.read_text(), flush=True)
                    except ProcessLookupError:
                        pass
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        _stop_group(process)
    code = 130 if interrupted else (124 if timed_out else process.returncode)
    (output / "result.json").write_text(json.dumps({
        "complete": not (timed_out or interrupted), "exitstatus": code,
        "timed_out": timed_out, "interrupted": interrupted,
        "last_progress": last,
        # The supervisor-measured seconds since the child last changed its
        # phase/node payload -- the number the stall decision was made on.
        "stalled_seconds": round(_MONOTONIC() - last_change, 3),
        "wall_seconds": _MONOTONIC() - started,
    }, indent=2) + "\n")
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())

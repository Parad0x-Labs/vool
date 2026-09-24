"""Bound stalled pytest phases without changing selection, ordering or verdicts.

A separate supervisor survives a stuck test, fixture, collection or interpreter
shutdown. Only its direct child can update progress; nested pytest processes
cannot hide an outer hang. Timeout is a red, incomplete run, never a skip.
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


def _progress(phase: str, node: str = "") -> None:
    if os.environ.get(_OWNER) != str(os.getpid()):
        return
    path = Path(os.environ[_PROGRESS])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "phase": phase,
                                     "node": node, "at": time.monotonic()}))
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


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--child"]:
        os.environ[_OWNER] = str(os.getpid())
        os.execvpe(args[1], args[1:], os.environ)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-timeout", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(args)
    if os.name != "posix":
        parser.error("the CI watchdog requires POSIX process groups")
    if not math.isfinite(options.phase_timeout) or options.phase_timeout <= 0:
        parser.error("phase timeout must be positive and finite")
    command = options.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required")
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
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
    started = time.monotonic()
    last = {"phase": "startup / plugin loading", "node": "", "at": started}
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
                    last = current
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if time.monotonic() - last["at"] > options.phase_timeout:
                timed_out = True
                print(f"CI STALL: {last['phase']} {last['node']} exceeded "
                      f"{options.phase_timeout:g}s; refusing an incomplete run", flush=True)
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
        "last_progress": last, "wall_seconds": time.monotonic() - started,
    }, indent=2) + "\n")
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())

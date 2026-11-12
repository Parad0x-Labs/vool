"""A pytest plugin that records which code units each proof actually executes.

This is the *observed* half of the governor. A declared edge ("this test proves that file")
is a claim; this plugin produces the evidence, by watching the interpreter and writing down
the code objects a test entered. Declared edges that the evidence never confirms are treated
as defects, not as documentation.

Deliberately stdlib-only: `sys.setprofile` fires on Python call events, which is enough to
identify the function entered, and costs far less than the line-level tracing a coverage tool
would install. Nothing here needs `coverage`, `pytest-cov`, or any package that is not already
a hard dependency of this repository.

Usage::

    pytest -p ops.impact.tracer --impact-trace-out=/tmp/trace.json tests/test_thing.py

Without ``--impact-trace-out`` the plugin registers and does nothing at all, so leaving it on
the command line of an ordinary run costs one import.

Known blind spots, stated rather than papered over:

* Code executed in a subprocess is invisible. A proof that drives the product through a
  subprocess must declare its surfaces in the registry instead of relying on observation.
* Threads already running when a test starts are not re-profiled; threads the test creates are.
* Module-scope units are attributed when the test executes something in the file or imports it
  during the test. Modules imported at collection time, before any test ran, belong to no
  single test and are not attributed to one.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from ops.impact.units import MODULE_SCOPE, index_units, qualname_for_line, unit_key

_EXCLUDED_PARTS = ("/.venv/", "/site-packages/", "/__pycache__/", "/.git/")

# The instrument does not measure itself. Pytest calls this module's own hooks while the
# profiler is installed, so without this every proof would appear to execute the tracer's
# teardown plumbing. Its behavior is governed as an unobservable surface instead -- see the
# `assets` field in the registry.
_SELF = str(Path(__file__).resolve())


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("impact")
    group.addoption(
        "--impact-trace-out",
        action="store",
        default=None,
        help="Write a change-impact execution trace (JSON) for this session to the given path.",
    )


class _Recorder:
    """Accumulates raw ``(filename, firstlineno)`` call sites for the test now running.

    Setup and call are recorded into separate buckets, and the difference matters more than it
    looks. Fixture setup in this repository builds a database, migrates it, writes memory files
    and loads preferences, so *every* test appears to execute `storage/db.py`. Treating that as
    an edge would rebuild the exact fan-out this governor exists to replace: change one storage
    helper, be told to run everything. The call bucket is what the proof itself does, and that
    is what an edge is allowed to be built from.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = str(repo_root)
        self._interesting: dict[str, bool] = {}
        self._sink: set[tuple[str, int]] = set()
        self._modules_before: set[str] = set()
        self.calls: dict[str, set[tuple[str, int]]] = {}
        self.setup_calls: dict[str, set[tuple[str, int]]] = {}
        self.modules: dict[str, set[str]] = {}
        self.setup_modules: dict[str, set[str]] = {}
        self.outcomes: dict[str, str] = {}
        self._active: str | None = None
        self._phase: str = "call"

    def _classify(self, filename: str) -> bool:
        if not filename.startswith(self._repo_root) or filename == _SELF:
            return False
        return not any(part in filename for part in _EXCLUDED_PARTS)

    def _profile(self, frame: Any, event: str, arg: Any) -> None:
        if event != "call":
            return None
        code = frame.f_code
        filename = code.co_filename
        keep = self._interesting.get(filename)
        if keep is None:
            keep = self._classify(filename)
            self._interesting[filename] = keep
        if keep:
            self._sink.add((filename, code.co_firstlineno))
        return None

    def start(self, nodeid: str, phase: str) -> None:
        if self._active is not None:
            self.stop()
        self._active = nodeid
        self._phase = phase
        bucket = self.setup_calls if phase == "setup" else self.calls
        self._sink = bucket.setdefault(nodeid, set())
        self._modules_before = set(sys.modules)
        threading.setprofile(self._profile)
        sys.setprofile(self._profile)

    def stop(self) -> None:
        if self._active is None:
            return
        sys.setprofile(None)
        threading.setprofile(None)
        nodeid = self._active
        phase = self._phase
        self._active = None
        imported = set(sys.modules) - self._modules_before
        bucket = self.setup_modules if phase == "setup" else self.modules
        files = bucket.setdefault(nodeid, set())
        for name in imported:
            module = sys.modules.get(name)
            origin = getattr(module, "__file__", None)
            if isinstance(origin, str) and self._classify(origin):
                files.add(origin)


_RECORDER: _Recorder | None = None
_OUT_PATH: Path | None = None
_ROOT: Path | None = None


def pytest_configure(config: Any) -> None:
    global _RECORDER, _OUT_PATH, _ROOT
    target = config.getoption("--impact-trace-out")
    if not target:
        return
    _ROOT = Path(str(config.rootpath)).resolve()
    _OUT_PATH = Path(target).expanduser().resolve()
    _RECORDER = _Recorder(_ROOT)


# `tryfirst` is declared through the marker, not by setting an attribute on the function.
# Current pytest no longer reads the legacy attribute form, and a hook that silently loses its
# ordering here does not fail loudly -- it just runs after pytest's own setup hook, so every
# fixture has already executed and its work lands in the call bucket instead of the setup one.
# That produces a proof map that looks fine and quietly restores the fan-out this design exists
# to remove, which is why the phase split is asserted behaviorally in the meta-proof.
@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: Any) -> None:
    if _RECORDER is not None:
        _RECORDER.start(item.nodeid, "setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: Any) -> None:
    # Closes the setup bucket and opens the call bucket immediately before `item.runtest()`.
    # Everything after this point is the proof itself.
    if _RECORDER is not None:
        _RECORDER.start(item.nodeid, "call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: Any) -> None:
    # Stops before fixture teardown. Teardown tears runtime state down; it is not part of what
    # the proof proves, and recording it would demand proofs for changes only cleanup touches.
    del item
    if _RECORDER is not None:
        _RECORDER.stop()


def pytest_runtest_logreport(report: Any) -> None:
    if _RECORDER is None:
        return
    previous = _RECORDER.outcomes.get(report.nodeid)
    if report.outcome == "failed" or previous == "failed":
        _RECORDER.outcomes[report.nodeid] = "failed"
    elif report.when == "call" or previous is None:
        _RECORDER.outcomes[report.nodeid] = report.outcome


def resolve_units(
    calls: dict[str, set[tuple[str, int]]],
    modules: dict[str, set[str]],
    repo_root: Path,
) -> dict[str, list[str]]:
    """Turn raw call sites into unit keys, one sorted list per proof."""
    span_cache: dict[str, Any] = {}
    resolved: dict[str, list[str]] = {}
    root = repo_root.resolve()

    def relative(filename: str) -> str | None:
        try:
            return Path(filename).resolve().relative_to(root).as_posix()
        except ValueError:
            return None

    def spans_for(rel: str) -> Any:
        cached = span_cache.get(rel)
        if cached is None:
            try:
                source = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                source = ""
            cached = index_units(source)
            span_cache[rel] = cached
        return cached

    for nodeid in set(calls) | set(modules):
        units: set[str] = set()
        touched_files: set[str] = set()
        for filename, firstlineno in calls.get(nodeid, set()):
            rel = relative(filename)
            if rel is None:
                continue
            touched_files.add(rel)
            units.add(unit_key(rel, qualname_for_line(spans_for(rel), firstlineno)))
        for filename in modules.get(nodeid, set()):
            rel = relative(filename)
            if rel is not None:
                touched_files.add(rel)
        for rel in touched_files:
            units.add(unit_key(rel, MODULE_SCOPE))
        resolved[nodeid] = sorted(units)
    return resolved


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    del session, exitstatus
    if _RECORDER is None or _OUT_PATH is None or _ROOT is None:
        return
    _RECORDER.stop()
    resolved = resolve_units(_RECORDER.calls, _RECORDER.modules, _ROOT)
    setup_resolved = resolve_units(_RECORDER.setup_calls, _RECORDER.setup_modules, _ROOT)
    payload = {
        "schema": 2,
        "proofs": {
            nodeid: {
                "outcome": _RECORDER.outcomes.get(nodeid, "unknown"),
                "units": resolved.get(nodeid, []),
                "setup_units": setup_resolved.get(nodeid, []),
            }
            for nodeid in sorted(set(resolved) | set(setup_resolved))
        },
    }
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

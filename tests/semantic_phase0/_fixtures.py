"""Shared fixtures for the Phase-0 suites, imported explicitly rather than discovered.

This is deliberately NOT a `conftest.py`. As one, its fixtures were invisible under CI's
sharded invocation -- `fixture 'make_agent_module' not found` on the shard that owned the
replay file, while the same file passed on its own and in small mixed runs. Adding
`__init__.py` did not fix it. Rather than keep guessing at pytest's conftest discovery from
a 226-file argument list, the fixtures are now plain functions that each test module imports
by name: an import either works or raises, and it behaves the same however pytest is invoked.

A module-scoped agent factory, for the suites that drive hundreds of turns.

`make_agent` in `tests/conftest.py` is function-scoped because it uses `monkeypatch`. The replay
proof needs one agent per turn across 504 turns inside a single fixture, so it cannot use a
function-scoped factory without either rebuilding the corpus per test or dropping to one comparison
per test file.

This is the same construction as `make_agent` -- the same three background loops silenced, the same
stub context loader -- assembled with `setattr` instead of `monkeypatch`. Nothing about the turn path
under test is stubbed: routing, the front door, the lanes, permission and the action-honesty validator are
all the shipped code.
"""
from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

from apps.vool_agent import VoolAgent
from tests import _network_seal as network_seal
from tests.conftest import make_stub_context

#: The message every refusal from this package carries, so a blocked call is attributable.
SEAL_REASON = "network blocked in tests/semantic_phase0"


@pytest.fixture(autouse=True, scope="module")
def block_outbound_network():
    """No turn in this package may reach the internet.

    Found the hard way. The replay proof compares response bytes across two configurations, and one
    corpus turn came back holding real search results -- current MDN and python.org pages, different
    on every run. The turn was answering from the live web, so its bytes could never be identical
    with anything, and the identity assertion failed for a reason that had nothing to do with
    instrumentation.

    The root conftest blocks `urlopen` to non-loopback hosts and local Ollama, but the live-info
    lookup lane reaches the network by a route those two do not cover. Blocking at the socket layer
    covers every HTTP client at once -- httpx, requests, urllib, anything -- because they all end up
    here.

    **Every** socket, not just non-loopback ones. The first version allowed loopback, which left
    local Ollama, a local daemon and any unix-socket service reachable -- a hostile review named
    that as a hole, and it is: "hermetic" that admits whatever happens to be listening on this
    machine makes the result depend on the machine.

    This is a control, not a reduced test: what is being proven is that instrumentation changes
    nothing, and a fetched sentence is not the runtime's output to compare. Every lane, gate and
    renderer still runs; the fetch fails, deterministically, in both configurations.

    Module-scoped, and therefore patched by hand rather than with `monkeypatch`. It has to be:
    pytest builds higher-scoped fixtures first, so a function-scoped block would not yet be in force
    when the module-scoped replay fixture drives its turns -- which is exactly when it is needed.
    """
    # The same pin `default_test_policy_disables_web_fallback` applies in the root conftest, re-applied
    # here at module scope. That fixture is function-scoped, and pytest builds higher-scoped fixtures
    # first -- so during a module-scoped fixture that drives turns, web fallback is still ON. That is
    # the actual reason a corpus turn came back holding live search results, and why this suite ran
    # for twelve minutes: every research turn was really trying the network and retrying the refusal.
    from core import policy_engine

    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base

    token = network_seal.push("semantic_phase0:deny-everything", network_seal.deny_everything(SEAL_REASON))
    try:
        yield
    finally:
        network_seal.release(token)
        policy_engine._POLICY_CACHE = previous_cache


@pytest.fixture(autouse=True)
def reseal_network_after_function_fixtures():
    """Kept as a name, no longer as a mechanism.

    This used to re-install a strict seal at function scope, on the belief that the root conftest's
    function-scoped guard was replacing the module-scoped one. Probing all three surfaces across
    IPv4/IPv6/unix/loopback showed it was not: that guard captured `socket.socket.connect` live and
    delegated to whatever was beneath it, so every target stayed refused. The reseal was fixing
    nothing, and "re-apply last" is ordering-dependent anyway -- it would have been the same class
    of accident, pointing the other way.

    The prohibition now composes in `tests/_network_seal`, where refusal is a union over an explicit
    policy stack and a later policy can only add refusals. This fixture asserts that, per test,
    rather than re-installing anything: the module's deny-everything policy is still on the stack
    and the union is still the thing on all three surfaces.
    """
    assert "semantic_phase0:deny-everything" in network_seal.active(), (
        f"this package's deny-everything policy is not active: {network_seal.active()!r}"
    )
    yield


@pytest.fixture(autouse=True, scope="module")
def pin_the_signing_key_passphrase():
    """The root conftest's key-storage pin, re-applied at module scope. Same gap as the policy pin.

    `tests/conftest.py` sets `VOOL_KEY_STORAGE_MODE=file` and a fixed `VOOL_KEY_PASSPHRASE` in an
    autouse fixture -- but a FUNCTION-scoped one, built with `monkeypatch`. pytest builds
    higher-scoped fixtures first, so neither is in force while the module-scoped fixtures below drive
    their turns. That is the identical ordering hole `block_outbound_network` documents for
    `allow_web_fallback`, and it hides here for one run longer because it is latent on a clean
    checkout.

    How it shows up. The signing key lives at `core/runtime_paths.py:VOOL_HOME` -- a module constant
    pinned to `PROJECT_ROOT/.vool_local`, so the disposable `VOOL_HOME` below does NOT move it and
    the key is written into the checkout. The FIRST run in a fresh checkout has no key, mints one,
    and encrypts it with the conftest passphrase from inside some function-scoped test. Every LATER
    run reads that encrypted record back during module-scoped turn driving, where the passphrase is
    not set, and the turn dies with `RuntimeError: Encrypted signing key record ... but
    VOOL_KEY_PASSPHRASE is not set`. Measured: five corpus turns raised on the second run of this
    package in one checkout, having answered normally on the first.

    So this suite was reproducible exactly once per checkout, which is worth rather less than a suite
    that is reproducible. Set here with the same values, restored after.
    """
    previous = {
        name: os.environ.get(name)
        for name in ("VOOL_KEY_STORAGE_MODE", "VOOL_KEY_PASSPHRASE")
    }
    os.environ["VOOL_KEY_STORAGE_MODE"] = "file"
    os.environ["VOOL_KEY_PASSPHRASE"] = "test-suite-only-never-a-real-key"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(autouse=True, scope="module")
def keep_the_checkout_clean(tmp_path_factory):
    """No test in this package may write into the repository.

    These suites drive real turns, and a `workspace_write` turn really writes a file -- into the
    current working directory, which is the checkout. It left a stray `notes.txt` at the repo root
    twice: once during the first replay run, and again during a full-suite run, where `git add -A`
    then committed it. Moving the whole package's cwd to a temp directory closes it for every test
    here at once, rather than per suite and per turn.
    """
    workdir = tmp_path_factory.mktemp("vool_semantic_cwd")
    home = tmp_path_factory.mktemp("vool_semantic_home")
    previous_cwd = os.getcwd()
    previous_home = os.environ.get("VOOL_HOME")
    # A disposable VOOL_HOME as well as a disposable cwd: the review asked for hermeticity against
    # the host home, and a turn that reads or writes the operator's real data dir is not hermetic
    # however clean the working directory is.
    os.environ["VOOL_HOME"] = str(home)
    os.chdir(workdir)
    try:
        yield workdir
    finally:
        os.chdir(previous_cwd)
        if previous_home is None:
            os.environ.pop("VOOL_HOME", None)
        else:
            os.environ["VOOL_HOME"] = previous_home


@pytest.fixture(scope="module")
def make_agent_module():
    """Build a fresh agent. Module-scoped so one fixture can drive a whole corpus.

    Also the root conftest's STORAGE pin, re-applied at module scope -- the same gap as the policy
    and key pins above. `runtime_storage_reset` pins the shared test database per TEST, but a
    module-scoped corpus fixture drives its turns during module setup, before any test's pin
    exists. Measured 2026-09-08 (shadow-equivalence module followed by the receipt-fidelity module
    in one process): those turns opened the disposable home's own store, created the lazily-built
    tables there (`task_trace_index`, ...) and flipped each store's module-level "table ready"
    memo, so the first later test on the pinned store died with `no such table: task_trace_index`
    -- the memo never rebuilds. Pinning here, before the first agent is built, sends every
    module-scoped drive to the same store the tests then read.
    """
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path, configure_default_db_path
    from storage.migrations import run_migrations
    from tests.conftest import _TEST_DB_PATH

    configure_default_db_path(_TEST_DB_PATH)
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()

    built: list[VoolAgent] = []

    def factory(
        *, backend_name: str = "test-backend", device: str = "test-device", persona_id: str = "default"
    ) -> VoolAgent:
        agent = VoolAgent(backend_name=backend_name, device=device, persona_id=persona_id)
        # The three background loops a test must not start: public presence sync, its heartbeat, and
        # the idle commons loop. Left running they outlive the test and reach the network.
        agent._sync_public_presence = lambda *args, **kwargs: None  # type: ignore[method-assign]
        agent._start_public_presence_heartbeat = lambda *args, **kwargs: None  # type: ignore[method-assign]
        agent._start_idle_commons_loop = lambda *args, **kwargs: None  # type: ignore[method-assign]
        agent.start()
        agent.context_loader.load = Mock(return_value=make_stub_context())  # type: ignore[assignment]
        built.append(agent)
        return agent

    return factory


@pytest.fixture(autouse=True, scope="module")
def pin_volatile_machine_observations():
    """One snapshot of mutable machine state, served identically to every replay run.

    Byte-identical equivalence asks whether the INSTRUMENTATION changed the answer. That question is
    only answerable if both sides observe the same world. Free disk space is not stable: the product
    renders it to one decimal GB (`core.runtime_execution_tools._machine_disk_usage` via
    `machine_diagnostics.disk_usage`), so any 100 MB of movement flips the rendered digit -- and an
    ordinary full-suite run moves far more than that while writing temp data.

    Measured on 2026-08-14 with the flag HELD CONSTANT at OFF: the same probe returned 23.2 GB, then
    22.9 GB after 250 MB of churn. Toggling `VOOL_SEMANTIC_REACH` moved free space by 0.000 MB. So
    the difference was never instrumentation; it was two independent samples of a drifting fact.

    The suite's existing same-configuration control cannot catch this. It re-runs the turn twice
    under OFF back-to-back, which detects JITTER but not DRIFT: two adjacent samples agree while the
    earlier OFF->ON gap straddled a real change, so the turn stays in the byte-compared set and the
    proof fails for a reason that has nothing to do with what it is proving.

    Pin the observation at its own product boundary rather than editing responses or loosening the
    comparison: the turn still routes through the real tool, the real renderer and the real response
    path, and only the reading of the outside world is held still. Anything genuinely caused by
    instrumentation still differs and still fails.
    """
    from core import machine_diagnostics

    snapshot = machine_diagnostics.disk_usage()
    original = machine_diagnostics.disk_usage

    def _pinned(*args, **kwargs):
        # Same shape the real probe returns, deep-copied so a caller that mutates a row cannot
        # change what the next run observes.
        return [dict(row) for row in snapshot]

    machine_diagnostics.disk_usage = _pinned
    try:
        yield
    finally:
        machine_diagnostics.disk_usage = original

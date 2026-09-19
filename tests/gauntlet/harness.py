"""Drive real conversations through the real public chat seam, with only the wire faked.

The 11k-test suite was green while the released app lost half of a user's request, resurrected a
stale audit, and turned an instruction into a city name. It was green because almost nothing in it
drives a CONVERSATION: tests call `parse_weather_entities()`, or `run_workspace_audit()`, or hand a
canned dict to a validator. Every one of those passes with the routing layer above it broken.

So this harness holds exactly one line still: **the model wire and the outbound network are fake;
everything above them is the shipped code path.** A turn here enters at
`core.web.api.service.dispatch_post` -- the same function the HTTP handler calls -- and goes through
the real session resolution, the real frontdoor, the real router, the real planner, the real
live-data runner, the real continuation state and the real honesty validators.

What is faked, and why each is the right depth:

* ``ModelRegistry.build_adapter`` -> `ScriptedModel`. The lowest seam in the model path
  (`memory_first_router` line ~1317 is its only routing-path caller). Provider SELECTION, ranking,
  fallback, retry and failure rendering all stay real; only the bytes coming back are scripted.
* ``tools.web.web_research.structured_weather_lookup`` -> a table. Note this sits BELOW
  `_run_weather_subtask`'s `_is_plausible_weather_location` guard and below the planner, so
  entity extraction is still genuinely under test -- when the planner invents a location, the fake
  is asked for it and the request is recorded, which is exactly how G8/G9 catch it.
* the market quote fetch, same reasoning.

Nothing else is patched. In particular the harness never fakes routing, arbitration, continuation,
capsule selection, or answer assembly -- those are the things being tested, and a suite that stubs
them is the suite we already have.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# One bootstrap per process. `bootstrap_runtime_services` seeds identity, credits and the provider
# registry; doing it per test would dominate the runtime and, worse, reset the very cross-turn state
# these scenarios exist to exercise.
_RUNTIME: Any = None
_RUNTIME_LOCK = threading.Lock()


# --------------------------------------------------------------------------------------- the wire


@dataclass
class ModelCall:
    provider_id: str
    model_name: str
    prompt: str
    task_kind: str


class ScriptedModel:
    """A deterministic model wire, shared by every adapter the router decides to build.

    Responses are matched by the FIRST rule whose predicate accepts the request, so a scenario can
    say "whatever you route to, answer arithmetic like this" without knowing which provider the
    router will pick. An exhausted or unmatched request falls to ``default``, which is a plain
    usable reply -- a scenario that cares about a failure must ask for one explicitly, so no test
    can pass because of an accidental empty response.
    """

    def __init__(self) -> None:
        self.calls: list[ModelCall] = []
        self.rules: list[tuple[Any, Any]] = []
        self.default: Any = "Understood."
        self._per_provider: dict[str, list[Any]] = {}
        self._install_standing_rules()

    def _install_standing_rules(self) -> None:
        """Structured policy calls that EVERY scripted wire must answer schema-validly.

        The entity-ambiguity probe makes a strict-JSON judgment call before an ordinary plain
        knowledge turn can reach its answering generation. Under this harness every model call
        returns the scenario's default text, so the probe's parse failed twice and the runtime
        correctly served its unresolved ask-back -- which meant the child's own self-test read
        "model wire not reached on a real turn" and every scenario in the group degraded to an
        infrastructure fault. The standing verdict ("not ambiguous") is what the probe's call
        shape exists to receive; a scenario that wants to script ambiguity replaces the rules.
        """
        from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

        system_marker = AMBIGUITY_SYSTEM_PROMPT[:80]
        self.rules.append(
            (
                lambda req: system_marker in str(getattr(req, "system_prompt", "") or ""),
                '{"ambiguous": false, "referents": [], "clarification": ""}',
            )
        )

    def reset(self) -> None:
        self.calls.clear()
        self.rules.clear()
        self._per_provider.clear()
        self.default = "Understood."
        self._install_standing_rules()

    def when(self, predicate, reply) -> None:
        """``predicate`` takes the ModelRequest; ``reply`` is text, an Exception, or a callable."""
        self.rules.append((predicate, reply))

    def when_prompt_contains(self, needle: str, reply) -> None:
        low = needle.lower()
        self.when(lambda req: low in str(getattr(req, "prompt", "")).lower(), reply)

    def queue_for_provider(self, provider_id: str, replies: list[Any]) -> None:
        """Consumed in order for that provider only -- the G10/G11 failover shape."""
        self._per_provider.setdefault(provider_id, []).extend(replies)

    def _resolve(self, manifest, request) -> Any:
        queued = self._per_provider.get(str(manifest.provider_id))
        if queued:
            return queued.pop(0)
        for predicate, reply in self.rules:
            try:
                if predicate(request):
                    return reply
            except Exception:
                continue
        return self.default


SCRIPT = ScriptedModel()


def _build_fake_adapter(_registry, manifest):
    from adapters.base_adapter import ModelAdapter, ModelResponse

    class _Scripted(ModelAdapter):
        def __init__(self, mf):
            self.manifest = mf

        def health_check(self):
            return {"ok": True, "provider_id": self.manifest.provider_id}

        def supports_streaming(self):
            return False

        def invoke(self, request):
            SCRIPT.calls.append(
                ModelCall(
                    provider_id=str(self.manifest.provider_id),
                    model_name=str(self.manifest.model_name),
                    prompt=str(getattr(request, "prompt", "")),
                    task_kind=str(getattr(request, "task_kind", "")),
                )
            )
            reply = SCRIPT._resolve(self.manifest, request)
            if callable(reply) and not isinstance(reply, str):
                reply = reply(request)
            if isinstance(reply, BaseException):
                raise reply
            if isinstance(reply, ModelResponse):
                return reply
            return ModelResponse(
                output_text=str(reply),
                confidence=0.9,
                raw_response={"model": self.manifest.model_name},
                usage={"prompt_tokens": 32, "completion_tokens": 32},
                provider_id=str(self.manifest.provider_id),
                model_name=str(self.manifest.model_name),
                provider_attested_model=str(self.manifest.model_name),
            )

    return _Scripted(manifest)


# ------------------------------------------------------------------------------- the outside world


@dataclass
class WeatherFact:
    temperature_c: float
    condition: str = "Clear"


# Only these resolve. Anything else the planner asks for is recorded and refused, which is how a
# phantom entity ("Summarize It", a whole instruction) becomes a visible test failure rather than a
# plausible-looking row in the answer.
KNOWN_WEATHER: dict[str, WeatherFact] = {
    "kaunas": WeatherFact(18.0, "Partly cloudy"),
    "tallinn": WeatherFact(12.0, "Overcast"),
    "riga": WeatherFact(14.0, "Clear"),
    "warsaw": WeatherFact(21.0, "Sunny"),
    "vilnius": WeatherFact(17.0, "Clear"),
}
KNOWN_MARKET: dict[str, dict[str, Any]] = {
    "bitcoin": {"price": 61000.0, "currency": "USD", "change_24h_pct": 3.4},
    "ethereum": {"price": 2400.0, "currency": "USD", "change_24h_pct": -1.2},
}

WEATHER_REQUESTS: list[str] = []
MARKET_REQUESTS: list[str] = []


def _fake_structured_weather_lookup(location, timeout_s: float = 0.0, **_kw):
    """Record what the runtime actually asked the weather provider for, then answer if it is real."""
    from types import SimpleNamespace

    asked = str(location or "")
    WEATHER_REQUESTS.append(asked)
    fact = KNOWN_WEATHER.get(asked.strip().lower().rstrip(","))
    if fact is None:
        return None  # same shape the real lookup uses for "no observation"
    return SimpleNamespace(
        condition=fact.condition,
        temperature_c=fact.temperature_c,
        feels_like_c=fact.temperature_c,
        high_c=fact.temperature_c + 2,
        low_c=fact.temperature_c - 4,
        place_label=asked.strip().title(),
        source_label="gauntlet-fixture",
        source_url="https://example.invalid/weather",
        observed_at="2026-08-07T12:00:00Z",
    )


# The machine the gauntlet runs on, stated rather than probed.
#
# `provider_hardware_fit_rejection` calls `_device_probe()` -> `core.hardware_tier.probe_machine()`,
# which is UNCACHED and shells out to `detect_gpu_devices()` on every ranking call, on every turn.
# Outside pytest that is 0.01s and reports this host honestly; inside pytest, under the network and
# subprocess guards and with the runtime's background threads competing for the same SQLite file, it
# is the difference between a candidate that ranks and a ranking that comes back empty -- which is
# then read one layer up as `local_available=False`, the `human` lane, and `no_ranked_provider`.
#
# So the MACHINE is virtualized and nothing else. Everything above this line runs for real:
# `provider_hardware_fit_rejection` still computes the rejection, `rank_provider_candidates` still
# filters on it with `enforce_hardware_fit=True`, `capability_truth` is still built from whatever
# survives, and Autopilot still picks the lane. A capable machine here means a candidate ranks
# because the real fit logic said it fits, not because anything was forced -- proved both ways by
# `test_an_incapable_machine_still_rejects_the_candidate`.
#
# 32 GB unified memory on Apple Silicon: comfortably above qwen3:8b's ~7.5 GB budget
# (8B * 0.75 + 1.5), and a shape this product actually ships on.
GAUNTLET_MACHINE_RAM_GB = 32.0
GAUNTLET_MACHINE_VRAM_GB = 32.0


def machine_probe(*, ram_gb: float = GAUNTLET_MACHINE_RAM_GB, vram_gb: float = GAUNTLET_MACHINE_VRAM_GB,
                  accelerator: str = "mps") -> Any:
    """A real `MachineProbe`, filled in with fixed numbers instead of a hardware probe."""
    from core.hardware_tier import MachineProbe

    return MachineProbe(
        cpu_cores=10,
        ram_gb=ram_gb,
        gpu_name="Gauntlet Fixture GPU",
        vram_gb=vram_gb,
        accelerator=accelerator,
        accelerator_status="ready",
        accelerator_advice="",
        gpu_devices=(),
        driver_version="gauntlet-fixture",
        vram_free_gb=vram_gb,
    )


def _quote(asset_key: str, asset_name: str, kind: str) -> Any:
    from core.live_quote_contract import LiveQuoteResult

    fact = KNOWN_MARKET.get(asset_key.lower(), {"price": 100.0, "currency": "USD", "change_24h_pct": 0.0})
    return LiveQuoteResult(
        asset_key=asset_key,
        asset_name=asset_name or asset_key.title(),
        symbol=asset_key.upper()[:4],
        value=float(fact["price"]),
        currency=str(fact["currency"]),
        as_of="2026-08-07T12:00:00Z",
        source_label="gauntlet-fixture",
        source_url="https://example.invalid/market",
        kind=kind,
        change_percent=float(fact["change_24h_pct"]),
    )


def _fake_crypto_multi(coin_ids, *, timeout_s: float = 0.0, **_kw):
    """Record which crypto the runtime actually asked for, then answer only for the real ones."""
    out = []
    for coin in list(coin_ids or []):
        key = str(coin).strip().lower()
        MARKET_REQUESTS.append(key)
        if key in KNOWN_MARKET:
            out.append(_quote(key, key.title(), "crypto"))
    return out


def _fake_market_multi(query, targets, *, timeout_s: float = 0.0, **_kw):
    """Same for commodities. `gold` is deliberately NOT in KNOWN_MARKET -- see G4.

    G4's whole point is that "Explain gold structure" must never reach this function. Recording the
    request rather than silently answering it is what turns that into an assertion: if the lexical
    GOLD=>MARKET fast path fires, `MARKET_REQUESTS` gains an entry and the scenario fails.
    """
    out = []
    for target in list(targets or []):
        key = str(getattr(target, "asset_key", target)).strip().lower()
        MARKET_REQUESTS.append(key)
        if key in KNOWN_MARKET:
            out.append(_quote(key, str(getattr(target, "asset_name", "") or key), "commodity"))
    return out


# Every live-data subtask the PLANNER produced this turn, whether or not it was executed. This is
# the plan-level evidence G7/G8/G9/G16 assert on: what the runtime decided to look up is a stronger
# statement than what happened to appear in the prose.
PLANNED_SUBTASKS: list[dict[str, Any]] = []
SUBTASK_CLOCKS: list[dict[str, Any]] = []


def install(monkeypatch, *, machine: Any | None = None) -> None:
    """Patch the wire, the network and the MACHINE for one test. Routing stays untouched."""
    from core.model_registry import ModelRegistry

    monkeypatch.setattr(ModelRegistry, "build_adapter", _build_fake_adapter, raising=True)

    # Virtualize the host. Patched on `core.hardware_tier` because `_device_probe` imports the
    # symbol inside its own body, so the module attribute is what it resolves.
    import core.hardware_tier as hardware_tier

    fixed = machine if machine is not None else machine_probe()
    monkeypatch.setattr(hardware_tier, "probe_machine", lambda: fixed, raising=True)

    import tools.web.web_research as web_research

    monkeypatch.setattr(
        web_research, "structured_weather_lookup", _fake_structured_weather_lookup, raising=False
    )
    # The runner imports the symbol inside the function body, so patching the module is enough --
    # asserted by `test_the_harness_actually_intercepts_the_weather_provider` in the suite.

    # The market wire. Without this `MARKET_REQUESTS` stayed empty no matter what the runtime did,
    # so G4's `assert turn.market_requests == []` was true for a reason that had nothing to do with
    # the app -- which is exactly why the lexical GOLD=>MARKET mutation survived it. A test that
    # asserts on a list nobody writes to is not a test.
    monkeypatch.setattr(web_research, "_crypto_price_fallback_multi", _fake_crypto_multi, raising=False)
    monkeypatch.setattr(web_research, "_market_quote_fallback_multi", _fake_market_multi, raising=False)

    # Observe the planner without changing it: wrap the real runner, record the plan it was handed
    # and the clocks it produced, then hand the call straight through.
    import core.agent_runtime.live_data_runner as live_runner

    real_run = live_runner.run_live_data_plan

    def _observed_run(plan, **kwargs):
        for subtask in list(getattr(plan, "subtasks", ()) or ()):
            PLANNED_SUBTASKS.append(
                {
                    "subtask_id": str(getattr(subtask, "subtask_id", "")),
                    "entity": str(getattr(subtask, "entity", "")),
                    "operation": str(getattr(subtask, "operation", "")),
                    "arguments": dict(getattr(subtask, "arguments", None) or {}),
                }
            )
        outcomes = real_run(plan, **kwargs)
        for outcome in list(outcomes or ()):
            SUBTASK_CLOCKS.append(
                {
                    "subtask_id": str(getattr(getattr(outcome, "subtask", None), "subtask_id", "")),
                    "started_at": getattr(outcome, "started_at", None),
                    "completed_at": getattr(outcome, "completed_at", None),
                    "state": str(getattr(outcome, "state", "")),
                }
            )
        return outcomes

    monkeypatch.setattr(live_runner, "run_live_data_plan", _observed_run, raising=True)

    # Bootstrap health-probes the real providers before any patch is in place, and the pytest
    # network guard fails those probes -- which trips the circuit breaker. Reset per test so each
    # scenario starts from a provider the router is willing to call.
    from core.model_health import reset_provider_health

    reset_provider_health()

    # Lazily-created tables whose "already created" flag is a MODULE GLOBAL while the database
    # underneath is per-test. `core.trace_id._init_table` sets `_TABLE_READY = True` after the
    # first CREATE; the next test gets a fresh database that has never seen that statement, the
    # flag still says ready, and the turn dies with
    # `OperationalError: no such table: task_trace_index` -> HTTP 500 -> an empty reply.
    #
    # This is the whole of the "model wire pollution" that blocked G3: it was never stale
    # specialist state and never the circuit breaker, it was a 500 that the driver was quietly
    # turning into "" and the scenarios were reading as a routing defect. Clearing the flag makes
    # the table be created against whatever database this test actually has.
    # Five modules share the pattern, so the reset is by shape rather than by name -- a sixth added
    # later would otherwise reintroduce the same phantom finding.
    for module_name in (
        "core.trace_id",
        "core.task_state_machine",
        "core.credit_ledger",
        "core.usage_meter",
        "core.mesh.credit_ledger",
    ):
        try:
            module = __import__(module_name, fromlist=["_"])
        except Exception:
            continue
        for attribute in dir(module):
            if not attribute.startswith("_"):
                continue
            if "READY" not in attribute and "INITIALIZED" not in attribute:
                continue
            try:
                if getattr(module, attribute) is True:
                    setattr(module, attribute, False)
            except Exception:
                continue

    # Re-register the providers this test's database is missing. THIS is what Blocker 1 actually
    # was, and it is neither the hardware probe nor provider health:
    #
    #   test 1, straight after bootstrap : ModelRegistry().list_manifests() -> ['ollama-local:qwen3:8b']
    #   test 2, same database path       : ModelRegistry().list_manifests() -> []
    #
    # The provider manifests are rows in the shared pytest database, the session fixture clears it
    # between tests, and `runtime()` bootstraps ONCE per process -- so every test after the first
    # ran against an EMPTY registry. Ranking then returns nothing (measured: `rank_provider_candidates`
    # comes back `[]` even with `enforce_hardware_fit=False`, so the fit gate was never the cause),
    # `capability_truth` is built from that empty list, `local_available` is False, Autopilot
    # resolves the `human` lane, and the turn ends `no_ranked_provider` /
    # "I couldn't get a live model response". Whichever test happened to run first looked fine,
    # which is precisely the order dependence that made the control untrustworthy.
    #
    # Registering through the runtime's own `ensure_default_provider` -- the same call bootstrap
    # makes -- restores the row the router reads. Routing is untouched: ranking, hardware-fit,
    # capability truth and Autopilot all run for real against a registry that simply is not empty.
    try:
        from core.model_registry import ModelRegistry
        from core.runtime_provider_defaults import default_runtime_model_tag
        from core.web.api.runtime import ensure_default_provider

        registry = ModelRegistry()
        ensure_default_provider(registry, default_runtime_model_tag())
        # The storage reset between tests also deletes the certification rows bootstrap wrote,
        # and bootstrap itself runs once per process -- so the restored default provider would
        # be refused by the authorship fence on every turn after the first test. Re-certify the
        # defaults here (the same authority bootstrap used); a scenario that wants to exercise
        # refusal registers its own uncertified model.
        from tests._authorship_certification import certify_for_authorship

        for _manifest in registry.list_manifests():
            try:
                certify_for_authorship(_manifest)
            except Exception:  # pragma: no cover - a manifest outside the probe's reach
                continue
    except Exception:
        pass

    SCRIPT.reset()
    WEATHER_REQUESTS.clear()
    MARKET_REQUESTS.clear()
    PLANNED_SUBTASKS.clear()
    SUBTASK_CLOCKS.clear()


# BLOCKING DEFECT IN THIS HARNESS -- the gauntlet POLLUTES the canonical suite.
#
# ============ CONTAINMENT PASS: INVENTORY + BISECT (not yet fixed) ============
#
# FULL INVENTORY of everything this harness mutates that can outlive a test:
#
#   RESTORED AUTOMATICALLY (monkeypatch-scoped, verified not to be the leak):
#     ModelRegistry.build_adapter · hardware_tier.probe_machine
#     web_research.structured_weather_lookup · _crypto_price_fallback_multi
#     _market_quote_fallback_multi · live_data_runner.run_live_data_plan
#
#   NOT RESTORED (candidate leaks):
#     1. os.environ VOOL_DAEMON_BIND_PORT / VOOL_DISABLE_MESH_DAEMON (setdefault, never undone)
#     2. `_RUNTIME` -- the bootstrapped RuntimeServices, never torn down
#     3. `reset_provider_health()` -- clears a global store (also done by tests/conftest.py)
#     4. schema READY flags forced False on 5 modules
#     5. `ensure_default_provider(...)` -- writes provider manifest rows
#     6. module-global capture lists (SCRIPT, WEATHER_REQUESTS, MARKET_REQUESTS, PLANNED_SUBTASKS,
#        SUBTASK_CLOCKS) -- harness-only, cleared per test
#     7. every in-memory side effect of `bootstrap_runtime_services` itself
#
# BISECT, in one pytest process against the two polluted target files:
#     targets alone ................................. 66 passed
#     import harness only ........................... 67 passed
#     env vars only (1 above) ....................... 67 passed   <- ENV IS NOT THE LEAK
#     H.runtime() only (bootstrap) .................. 3 failed    <- BOOTSTRAP IS THE LEAK
#
# So candidates 1 and 6 are ruled OUT by measurement, and DB rows are ruled out too:
# `tests/conftest.py::runtime_storage_reset` is function-scoped autouse and already DELETEs every
# RUNTIME_TABLE, resets memory files and re-saves default preferences between tests. The leak is
# bootstrap's IN-MEMORY / out-of-database state.
#
# THE ONE NEXT LEAKED STATE, traced (stop point for this pass):
#   `bootstrap_runtime_services` calls `ensure_public_hive_auth(...)` at core/web/api/runtime.py:591
#   and stores `public_hive_auth_snapshot(...)` on the runtime. Afterwards
#   `tests/test_public_hive_bridge.py` gets REAL values back from `load_public_hive_bridge_config()`
#   -- auth_token='set-via-env-before-startup', meet_seed_urls=('https://203.0.113.11:8766', ...),
#   tls_ca_file='/tmp/cluster-ca.pem', tls_insecure_skip_verify=True -- instead of the values it
#   mocks via `_discover_local_cluster_bootstrap`. No such file exists on this host
#   (/etc/vool-hive-mind/watch-config.json is absent), so the values are held in memory, and the
#   test's mock of the module-level name is bypassed once bootstrap has run.
#   The third failure (test_must_keep_is_a_guarantee_not_a_sort_key) is a SEPARATE leak in the
#   persona/context-budget state and has not been traced yet.
#
# NEXT ACTION: contain `ensure_public_hive_auth`'s state (snapshot/restore around the gauntlet's
# bootstrap, or give the gauntlet session its own public-hive state), re-run the A/B/C ordering
# repro, then trace the context-budget leak the same way. Do NOT weaken the three failing tests --
# they are correctly catching contamination.
#
# ORIGINAL MEASUREMENT:
#
#   pytest tests/test_public_hive_bridge.py tests/test_must_keep_is_a_guarantee_not_a_sort_key.py
#       -> 66 passed
#   pytest tests/gauntlet/ <those same two files>
#       -> 3 failed
#   canonical full suite at this tip -> 3 failed, 11681 passed
#   the same three files at the released SHA, in isolation -> 51 passed
#
# So the failures are this harness leaking, not a release defect and not pre-existing flake. The
# leak is process-global state that `runtime()` establishes once and never tears down. Prime
# suspects, in order:
#   * `os.environ.setdefault("VOOL_DISABLE_MESH_DAEMON", ...)` / `VOOL_DAEMON_BIND_PORT` -- set
#     for the process and never restored, so every later test in the run sees them;
#   * the bootstrapped `RuntimeServices` itself (seeds identity, credits, provider rows, threads);
#   * the per-test provider re-registration writing manifest rows other suites do not expect.
#
# A release gate that breaks the suite it ships alongside is not usable, so this must be closed
# before the gauntlet can gate anything. The fix is almost certainly to confine the environment
# mutation and the bootstrap to the gauntlet's own session (an autouse fixture that restores
# os.environ, or a dedicated pytest marker/subprocess), NOT to weaken the assertions in the three
# tests that are failing -- they are correct and they are catching real contamination.

def runtime() -> Any:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            os.environ.setdefault("VOOL_DAEMON_BIND_PORT", "49188")
            # The mesh daemon must not run under the gauntlet. `bootstrap_runtime_services` starts
            # `VoolDaemon`, whose `_run_order_book_loop` thread queries `task_assignments` and
            # writes `audit_log` -- neither table exists in a test database, so the loop spins
            # raising `OperationalError` and hammering the same SQLite file every turn needs.
            # That contention is what made whole pytest runs hang (two runs killed at 10 and 7
            # minutes) and what made ranking intermittently come back empty; the runs that were
            # fast all happened to carry this flag in the shell, and the runs that hung did not.
            # Setting it here rather than relying on the caller's environment is the difference
            # between a deterministic suite and one that depends on how it was invoked.
            # `core/web/api/runtime.py:751` reads it; nothing about routing changes.
            os.environ.setdefault("VOOL_DISABLE_MESH_DAEMON", "1")
            from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
            from core.web.api.runtime import bootstrap_runtime_services

            global _BOOTSTRAP_COUNT
            _BOOTSTRAP_COUNT += 1
            _RUNTIME = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version=VOOL_WORKSTATION_DEPLOYMENT_VERSION,
                run_prewarm=False,
            )
            # `core.final_answer_authorship` refuses an uncertified LOCAL model before its
            # adapter is built, and every provider this harness seeds is a loopback model. Left
            # uncertified, the scripted wire is never reached: the child's own self-test reports
            # "model wire not reached on a real turn" and every scenario in the group reads as an
            # INFRASTRUCTURE FAULT. Certifying the seeded registry is what an operator does for
            # their own local models; the authority itself is untouched, and a scenario that
            # wants to exercise refusal registers its own uncertified model.
            from core.model_registry import ModelRegistry
            from tests._authorship_certification import certify_for_authorship

            for _manifest in ModelRegistry().list_manifests():
                try:
                    certify_for_authorship(_manifest)
                except Exception:  # pragma: no cover - a manifest outside the probe's reach
                    continue
        return _RUNTIME


# ------------------------------------------------------------------------------------ the driver


@dataclass
class Turn:
    """Everything one real turn produced, so an assertion can name evidence, not prose."""

    said: str
    turn_id: str
    session_id: str
    status: int
    reply: str
    payload: dict[str, Any] = field(default_factory=dict)
    model_calls: list[ModelCall] = field(default_factory=list)
    weather_requests: list[str] = field(default_factory=list)
    market_requests: list[str] = field(default_factory=list)
    activity: list[dict[str, Any]] = field(default_factory=list)
    planned_subtasks: list[dict[str, Any]] = field(default_factory=list)
    subtask_clocks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def low(self) -> str:
        return self.reply.lower()

    def mentions(self, *needles: str) -> bool:
        return all(n.lower() in self.low for n in needles)

    def missing(self, *needles: str) -> list[str]:
        return [n for n in needles if n.lower() not in self.low]

    def numbers(self) -> list[str]:
        return re.findall(r"-?\d[\d,]*\.?\d*", self.reply)

    def has_number(self, value: float, tol: float = 0.51) -> bool:
        for raw in self.numbers():
            try:
                if abs(float(raw.replace(",", "")) - value) <= tol:
                    return True
            except ValueError:
                continue
        return False

    def planned_entities(self, operation: str = "") -> list[str]:
        """What the PLANNER decided to look up -- stronger evidence than what the prose shows."""
        return [
            str(item["entity"])
            for item in self.planned_subtasks
            if not operation or str(item["operation"]) == operation
        ]

    def proves_parallel_overlap(self) -> bool:
        """Real clock overlap between two subtasks. Model prose claiming parallelism is not evidence."""
        spans = [
            (float(c["started_at"]), float(c["completed_at"]))
            for c in self.subtask_clocks
            if c.get("started_at") is not None and c.get("completed_at") is not None
        ]
        return any(
            a_start < b_end and b_start < a_end
            for index, (a_start, a_end) in enumerate(spans)
            for (b_start, b_end) in spans[index + 1 :]
        )

    def activity_of(self, *event_types: str) -> list[dict[str, Any]]:
        wanted = {e.lower() for e in event_types}
        return [e for e in self.activity if str(e.get("event_type", "")).lower() in wanted]

    def tools_run(self) -> list[str]:
        out = []
        for event in self.activity:
            name = str(event.get("tool_name") or "").strip()
            if name and str(event.get("event_type", "")).startswith("tool_"):
                out.append(name)
        return out

    def describe(self) -> str:
        return (
            f"\n  said        : {self.said!r}"
            f"\n  reply       : {self.reply[:600]!r}"
            f"\n  weather asks: {self.weather_requests}"
            f"\n  market asks : {self.market_requests}"
            f"\n  planned     : {[(i['operation'], i['entity']) for i in self.planned_subtasks]}"
            f"\n  model calls : {len(self.model_calls)}"
            f"\n  tools       : {self.tools_run()}"
            f"\n  route       : {self.payload.get('route')!r} / {self.payload.get('route_reason')!r}"
        )


class Conversation:
    """One chat, many turns, real session continuity between them."""

    def __init__(self, name: str = "") -> None:
        self.key = f"gauntlet-{name or uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:6]}"
        self.turns: list[Turn] = []
        self.session_id = ""

    def say(self, text: str, *, turn_id: str = "", workspace: str | Path | None = None) -> Turn:
        # Per TURN, not just per test. The scripted wire is always available by construction, but
        # the real provider-health store does not know that: background probes against the blocked
        # Ollama endpoint record failures mid-conversation, `provider_capability_truth_for_manifest`
        # turns that into `availability_state="blocked"`, and the autopilot then resolves the
        # `human` lane -- `no_ranked_provider`, "I couldn't get a live model response". That is the
        # harness lying to the router about its own fixture, not the app misrouting, so the health
        # store is kept consistent with the fixture on every turn. Routing itself is untouched: the
        # autopilot still makes its own decision, it just makes it against a provider that really
        # is reachable here.
        from core.model_health import reset_provider_health
        from core.web.api.service import dispatch_post

        reset_provider_health()

        tid = turn_id or f"t{len(self.turns) + 1}-{uuid.uuid4().hex[:6]}"
        before_weather = len(WEATHER_REQUESTS)
        before_market = len(MARKET_REQUESTS)
        before_calls = len(SCRIPT.calls)
        before_plan = len(PLANNED_SUBTASKS)
        before_plan_clocks = len(SUBTASK_CLOCKS)

        body: dict[str, Any] = {
            "model": "gauntlet",
            "stream": False,
            "turn_id": tid,
            "session_id": self.key,
            "messages": [{"role": "user", "content": text}],
        }
        if workspace is not None:
            body["workspace"] = str(workspace)
        root = str(workspace or PROJECT_ROOT)
        response = dispatch_post(
            path="/api/chat",
            body=body,
            headers={},
            runtime=runtime(),
            model_name="gauntlet",
            workspace_root_provider=lambda: root,
            client_host="127.0.0.1",
        )
        status = int(getattr(response, "status", 0) or 0)
        payload: dict[str, Any] = {}
        raw = getattr(response, "body", b"")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        if isinstance(raw, str) and raw.strip():
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"_unparsed": raw[:2000]}
        # A non-200 is infrastructure, never a behavioural finding, and it must never reach an
        # assertion as an empty string. A 500 from a missing table read exactly like "the router
        # produced no answer", and three scenarios were written up as release defects on the
        # strength of it. Fail loudly and name the cause instead.
        if status != 200:
            raise AssertionError(
                f"HARNESS/INFRASTRUCTURE FAILURE, not a finding: HTTP {status} on turn {tid!r}\n"
                f"  said : {text!r}\n"
                f"  error: {payload.get('error') or str(raw)[:400]!r}\n"
                "Fix the harness or the environment; do not interpret this as app behaviour."
            )
        reply = str((payload.get("message") or {}).get("content") or payload.get("response") or "")
        self.session_id = str(payload.get("vool_session_id") or self.session_id)

        activity: list[dict[str, Any]] = []
        if self.session_id:
            try:
                from core.runtime_continuity import list_recent_runtime_session_events

                activity = [
                    event
                    for event in list_recent_runtime_session_events(self.session_id, limit=200)
                    if str(event.get("client_turn_id") or "") == tid
                ]
            except Exception:
                activity = []

        turn = Turn(
            said=text,
            turn_id=tid,
            session_id=self.session_id,
            status=status,
            reply=reply,
            payload=payload,
            model_calls=list(SCRIPT.calls[before_calls:]),
            weather_requests=list(WEATHER_REQUESTS[before_weather:]),
            market_requests=list(MARKET_REQUESTS[before_market:]),
            planned_subtasks=list(PLANNED_SUBTASKS[before_plan:]),
            subtask_clocks=list(SUBTASK_CLOCKS[before_plan_clocks:]),
            activity=activity,
        )
        self.turns.append(turn)
        return turn


__all__ = [
    "KNOWN_MARKET",
    "KNOWN_WEATHER",
    "MARKET_REQUESTS",
    "PLANNED_SUBTASKS",
    "SCRIPT",
    "SUBTASK_CLOCKS",
    "WEATHER_REQUESTS",
    "Conversation",
    "ModelCall",
    "Turn",
    "install",
    "runtime",
]


# NOTE FOR WHOEVER PICKS THIS UP -- two known harness defects, both recorded rather than papered over:
#
# 1. THE MODEL WIRE IS NOT REACHED AFTER A LIVE-DATA TURN -- NARROWED, NOT CLOSED.
#    Progress this session: the EMPTY replies were HTTP 500s
#    (`no such table: task_trace_index`, then `task_state_events`) caused by module-global
#    "schema already created" flags outliving the per-test database. Both are fixed above, and
#    `Conversation.say` now RAISES on any non-200 so an infrastructure fault can never again be
#    read as app behaviour -- that is exactly how three scenarios were mis-reported as release
#    defects last round.
#    What remains: with the 500s gone the failure is now a genuine routing outcome. The runtime's
#    own Activity says
#        task_classified      : Task classified as unknown.
#        model_routing_started: Autopilot routed normalization_assist through the HUMAN lane.
#        model_routing_failed : reason='no_ranked_provider' ranked=[]
#    against the working case, which routes the same task_kind through the DAILY lane with
#    ranked=['ollama-local:qwen3:8b']. `local_inference_autopilot._resolve_lane` returns "human"
#    when `local_available` is False, and that is
#    `any(item.locality == "local" and item.availability_state != "blocked" for item in capabilities)`.
#    RULED OUT: the model_health circuit breaker (snapshot at failure time shows
#    circuit_open=False, consecutive_failures=0, and resetting per test AND per turn changes
#    nothing). So the capability tuple reaching the autopilot is empty or all-blocked for a
#    reason not yet found -- start at whatever supplies `capability_truth` on the turn path.
#    ORDERING MATTERS: a conversation whose FIRST turn is live-data fails only when it is not the
#    first test in the process; run alone, the identical sequence passes.
#    NEW LEAD (this session, from tracing the supplier): `capability_truth` on the turn path is
#    built in `memory_first_router` (~2785) as
#        capability_truth = hydrate_capability_truth_with_benchmarks(
#            tuple(provider_capability_truth_for_manifest(e) for e in ranked_manifests))
#    so an empty capability tuple means RANKED_MANIFESTS was empty, not that availability was
#    computed wrong. `ranked_manifests` comes from `rank_provider_candidates(...,
#    enforce_hardware_fit=True)`, which drops any manifest whose
#    `provider_capability_truth_for_manifest(m).hardware_fit_reason` is non-empty
#    (core/provider_routing.py:167-171). That reason comes from
#    `provider_hardware_fit_rejection`, whose first act is `_device_probe()`.
#    MEASURED: calling `rank_provider_candidates` / `_device_probe()` directly from a test HANGS
#    (two runs killed at 10min and 7min with no output at all). A hardware probe that blocks or
#    times out under the pytest network guard would explain the intermittency exactly: whichever
#    turn happens to get a usable probe result ranks a candidate and lands the daily lane; a turn
#    that gets None/partial ranks nothing, and `local_available` is then False purely because the
#    candidate list is empty. That is an ENVIRONMENT fault, and it is consistent with the ordering
#    dependence.
#    NEXT STEP, do this first: make `_device_probe()` deterministic for the gauntlet (a fixed
#    MachineProbe with real-looking ram/vram/accelerator) and re-run the A/B/C/D/E control. That
#    keeps ranking, hardware-fit and Autopilot fully real -- it only stops the probe reaching
#    hardware that pytest has walled off. Do NOT force the lane or inject a provider.
#    CAUTION: this may NOT be a harness defect. The addendum's G16 describes the same signature
#    (unknown -> normalization_assist -> human lane, no planner, no tool loop) as a RELEASE defect.
#    Until the capability source is traced, neither reading is established, and no scenario may
#    claim a release defect on this evidence.
#
# 1b. ORIGINAL NOTE. Standalone, a general-knowledge prompt
#    reaches `ScriptedModel`. Once any earlier turn in the same process has taken the live-data
#    lane, later turns answer "I couldn't get a live model response" and the wire is never called.
#    `core.model_health.reset_provider_health()` is already called per test and does NOT fix it, so
#    the circuit breaker is not the cause. Until this is isolated, NO model-prose scenario may claim
#    a release defect -- the control fails for the same reason as the subject. The evidence-based
#    scenarios (what the provider was asked for, which numbers reached the answer) are unaffected.
#
# 2. `Turn.model_calls` reports 0 even on turns whose reply demonstrably came from the scripted
#    wire. The slice arithmetic looks right; the count is not trustworthy yet. Assert on replies and
#    on `WEATHER_REQUESTS`, not on `len(turn.model_calls)`, until this is fixed.


# ----------------------------------------------------------------- hermetic child-side helpers

_BOOTSTRAP_COUNT = 0


def bootstrap_count() -> int:
    """How many times this interpreter has bootstrapped the runtime.

    A scenario group asserts this is 1: that is the machine-checkable form of "ALPHA and BETA shared
    one runtime", which is the only condition under which cross-chat isolation means anything.
    """
    return _BOOTSTRAP_COUNT


class _Unmanaged:
    """monkeypatch's setattr without the undo -- the child process is the undo."""

    @staticmethod
    def setattr(target, name, value, raising=True):
        if raising and not hasattr(target, name):
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        setattr(target, name, value)


def install_unmanaged(*, machine: Any | None = None) -> None:
    """`install()` for a process that will not outlive the test.

    Deliberately the SAME code path as the pytest-managed install, so the child cannot drift into
    testing a different set of seams than the in-process control suite does.
    """
    install(_Unmanaged(), machine=machine)


def reset_capture() -> None:
    SCRIPT.reset()
    WEATHER_REQUESTS.clear()
    MARKET_REQUESTS.clear()
    PLANNED_SUBTASKS.clear()
    SUBTASK_CLOCKS.clear()

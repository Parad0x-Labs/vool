from __future__ import annotations

import subprocess

# C15 (2026-09-03): the ONE bounded unattended preflight runs BEFORE any runtime module
# is imported — it pins this pytest session to non-interactive storage (vault + file
# signer, no inherited Keychain grant) and selects the isolated bundled Chromium, so no
# collection-time import or fixture can reach the macOS keyring, the `security` CLI or a
# signed-in browser profile first. Placement here is load-bearing: moving this call below
# the runtime imports is exactly the sabotage the preflight tests check for.
from core.unattended_preflight import preflight as _unattended_preflight

_unattended_preflight("pytest-conftest")

# Must be bound before any project import can run
# core.windows_quiet_subprocess.enable_quiet_subprocess(), which replaces this initializer
# process-wide and keeps no reference to the original.
_PRISTINE_POPEN_INIT = subprocess.Popen.__init__

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError
from urllib.parse import urlsplit

import pytest
import pytest as _pytest

from apps.vool_agent import VoolAgent
from core import local_ollama_inventory
from core.memory_first_router import ModelExecutionDecision
from core.persistent_memory import (
    conversation_log_path,
    ensure_memory_files,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.public_hive import client as public_hive_client
from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
from core.user_preferences import default_preferences, save_preferences
from storage.db import active_default_db_path, configure_default_db_path, get_connection, reset_default_connection
from storage.migrations import run_migrations
from tests import _network_seal as network_seal

# Plugin-discovery hermeticity (2026-09-19). The suite used to be clean only by accident: the
# default external root ~/Desktop/Vool-skills-plugins happened not to exist on dev machines, so
# every un-isolated probe reported MISSING. The legacy-folder reuse rule (Nulla-skills-plugins,
# the pre-rename installation) plus the bundled source (the repo's own plugins/) made that
# assumption false: an un-isolated test would probe the operator's REAL Desktop tree and see the
# owner's packs, and the repo's bundled packs would ride into every turn. Pin the session to a
# Desktop-free, bundle-free world unless a test (or an operator) explicitly sets either
# variable: setdefault, so monkeypatch.setenv and a deliberate env still win.
_HERMETIC_PLUGINS = Path(tempfile.mkdtemp(prefix="vool_pytest_plugins_"))
os.environ.setdefault("VOOL_PLUGINS_DIR", str(_HERMETIC_PLUGINS / "no-desktop-tree"))
os.environ.setdefault("VOOL_BUNDLED_PLUGINS_DIR", str(_HERMETIC_PLUGINS / "no-bundled-tree"))

# ------------------------------------------------------------------ collection integrity
#
# pytest 9.1.x re-collects a package once per terminal FILE argument whose parent it is:
# `Session.collect` bypasses the collection cache for the file's parent (handle_dupes=False,
# so duplicate file arguments keep working), and the re-collected parent then mints FRESH
# Directory collectors for every subpackage (Package.collect has no per-path dedup) and
# overwrites the cached children. Conftest fixtures, however, stay bound to the FIRST
# collector object that loaded them (FixtureManager pops _pending_conftests exactly once),
# and `_matchfactories` in 9.1.x matches by node IDENTITY first — so items collected under a
# later duplicate fail with "fixture 'wallet_env' not found" for the rest of the session.
# Measured on the CI shard runner (run 35561240691: pytest 9.1.0 AND 9.1.1, relative AND
# absolute file args — tests/wallet carried 7 distinct Package objects, tests/usepod 4,
# 283 CI "fixture not found" errors) and locally with interleaved top-level/subpackage file
# arguments. Keeping ONE collector per directory path per session restores the identity the
# fixture registry already assumes. Upstream report: https://github.com/pytest-dev/pytest/issues/15071.


def pytest_collectstart(collector):
    # pytest.Directory is the shared base of Dir (plain directory) and Package
    # (__init__.py directory) — exactly the collectors whose identity must be stable.
    if isinstance(collector, pytest.Directory):
        by_path = collector.session.__dict__.setdefault("_vool_directory_collectors", {})
        by_path.setdefault(collector.path, collector)


def pytest_collect_directory(path, parent):
    # Firstresult hook: hand back the already-created collector for this path so a
    # re-collected parent reuses it instead of minting a duplicate; None lets pytest's
    # default implementation create the collector and record it via pytest_collectstart.
    first = getattr(parent.session, "_vool_directory_collectors", {}).get(path)
    return first or None

# storage/db.py has no pytest-specific path override, so without this, every test in
# this session would share the SAME on-disk SQLite file a live `apps.vool_api_server`
# process may be connected to (active_default_db_path() resolves to the real runtime
# data dir). runtime_storage_reset below DELETEs every runtime table - including wallet
# and credit ledgers - before each test, which would wipe rows out from under a running
# server; concurrent writes to the same file from two processes also produced
# intermittent Windows access-violation crashes during full-suite runs. Re-applied at
# the top of runtime_storage_reset (not just once here) so a test that resets the
# override (e.g. test_storage_db_pooling.py calling configure_default_db_path(None))
# can't leave later tests pointed at the live path for the rest of the session.
_TEST_DB_PATH = str(Path(tempfile.mkdtemp(prefix="vool_pytest_db_")) / "vool_web0_v2_test.db")

IMMUTABLE_RUNTIME_TABLES = (
    "context_capsule_heads",
    "context_capsule_versions",
    "provider_invocation_manifests",
    "provenance_manifests",
)

RUNTIME_TABLES = (
    "context_import_grants",
    "context_namespaces",
    "adaptive_lexicon",
    "audit_log",
    "compute_credit_ledger",
    "contribution_ledger",
    "dialogue_sessions",
    "dialogue_turns",
    "curiosity_runs",
    "curiosity_topics",
    "dna_wallet_ledger",
    "dna_wallet_profiles",
    "dna_wallet_security",
    "wallet_profiles",
    "wallet_proposals",
    "wallet_proposal_events",
    "wallet_spend_ledger",
    "wallet_limits",
    "wallet_card_tokens",
    "wallet_receipts",
    "wallet_signing_requests",
    "wallet_x402_bindings",
    "wallet_controls",
    "wallet_facilitators",
    "wallet_unlock_attempts",
    "wallet_quotes",
    "wallet_transfers",
    "wallet_usepod_operations",
    "response_feedback",
    "event_log_v2",
    "fault_records",
    "hive_idempotency_keys",
    "knowledge_holders",
    "knowledge_manifests",
    "learning_shards",
    "local_tasks",
    "model_provider_manifests",
    "reminder_requests",
    "notification_items",
    "notification_deliveries",
    "notification_preferences",
    "calendar_accounts",
    "calendar_selections",
    "calendar_event_projections",
    "native_notification_requests",
    "runtime_checkpoints",
    "runtime_session_events",
    "runtime_sessions",
    "runtime_tool_receipts",
    "security_events",
    "session_hive_watch_state",
    "session_memory_policies",
    "shard_reuse_outcomes",
    "swarm_dispatch_budget_events",
    "web_notes",
)

MEMORY_TEMPLATE = (
    "# VOOL Persistent Memory\n\n"
    "## Identity\n\n"
    "- **My name**: VOOL\n"
    "- **Owner's name**: unknown\n\n"
    "## Privacy Pact\n\n"
    "- Not set yet.\n\n"
    "## Learned Knowledge\n\n"
)

FORBIDDEN_CHAT_LEAKS = (
    "invalid tool payload",
    "missing_intent",
    "i won't fake it",
    "traceback",
)


def normalize_response_text(text: str) -> str:
    return " ".join(str(text or "").split())


def make_stub_context(
    *,
    local_candidates: list | None = None,
    swarm_metadata: list | None = None,
    retrieval_confidence_score: float = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        local_candidates=list(local_candidates or []),
        swarm_metadata=list(swarm_metadata or []),
        retrieval_confidence_score=float(retrieval_confidence_score),
        assembled_context=lambda: "",
        context_snippets=lambda: [],
        report=SimpleNamespace(
            retrieval_confidence=float(retrieval_confidence_score),
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )


def memory_hit_decision(*, output_text: str = "", trust_score: float = 0.82) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="memory_hit",
        task_hash="test-memory-hit",
        output_text=output_text,
        confidence=trust_score,
        trust_score=trust_score,
        used_model=False,
        validation_state="not_run",
    )


@pytest.fixture(autouse=True)
def unpatched_subprocess_popen() -> None:
    """Restore the stock `subprocess.Popen.__init__` before every test.

    `core.windows_quiet_subprocess.enable_quiet_subprocess()` monkeypatches
    `subprocess.Popen.__init__` process-wide to force CREATE_NO_WINDOW, and never
    restores it. `apps.vool_api_server` calls it at import time, so any test module
    that imports that server - directly or through a helper such as
    `tests.gauntlet._live` - silently applies the patch to the WHOLE pytest session at
    collection time. On Windows a console child denied a console of its own still
    inherits this process's console std handles, which it then cannot write to: the
    child's first write fails, so e.g. `bash -e installer/bootstrap_vool.sh` dies
    with status 1 and no output. That made
    tests/test_bootstrap_install_contract.py's subprocess tests pass alone and fail
    under a sweep that collected an importer first (issue #23). Process-global patches
    must not survive across tests.
    """
    if getattr(subprocess.Popen, "_vool_quiet_patched", False):
        subprocess.Popen.__init__ = _PRISTINE_POPEN_INIT  # type: ignore[method-assign]
        subprocess.Popen._vool_quiet_patched = False  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def summarizer_cache_reset() -> None:
    """Drop the summary cache and the memoized model pick between tests.

    Both are module-global, so a summary or a model choice made under one test's stubs would
    otherwise answer for the next one -- the same cross-test leakage that made the bootstrap
    contract tests fail only when something else ran first.
    """
    from core import conversation_summarizer

    conversation_summarizer.reset_summary_cache()
    yield
    conversation_summarizer.reset_summary_cache()


@pytest.fixture(autouse=True)
def machine_followup_cache_reset() -> None:
    """Drop the in-process last-machine-read cache between tests.

    The other half of the same memory lives in the `machine_read_memory` table, which
    `reset_runtime_continuity_state()` now clears. Both halves have to go: the recall path reads
    the cache first and falls back to the table, so clearing either one alone still lets a disk
    read performed by one test decide how a later test's "ok what about D?" routes.

    Only three test modules cleared this by hand, and `reset_machine_followup_state()` is
    deliberately cache-only -- test_machine_read_memory.py calls it to SIMULATE a restart and
    then asserts the table still answers. So the per-test isolation belongs here, not in that hook.
    """
    from core.agent_runtime import fast_paths_machine

    fast_paths_machine.reset_machine_followup_state()
    yield
    fast_paths_machine.reset_machine_followup_state()


@pytest.fixture(autouse=True)
def provider_health_reset() -> None:
    """Drop the provider circuit-breaker state between tests.

    `core.model_health._HEALTH` is a plain module-level dict with no expiry of its own, and
    `rank_providers` subtracts 10.0 from any manifest whose circuit is open
    (`core/model_selection_policy.py`). The lane-fit margin that decides ordinary chat between the
    general and the reasoning tier is 0.55, so one open circuit left behind by an earlier test
    silently reverses the answer: with `ollama-local:qwen2.5:7b` tripped,
    `test_ordinary_chat_routes_to_the_general_model` selects `deepseek-r1:14b` instead. A circuit
    opens after five consecutive failures and closes on a 20-second cooldown, which is why the
    contamination window is narrow and the failure reads as intermittent rather than ordered.

    Cache-only and per test, deliberately: the reset runs at the test BOUNDARY, never inside a
    test, so recording failures until a circuit opens and observing the ranking change still works
    exactly as before -- see `test_provider_health_is_isolated_between_tests.py`, which pins both
    halves. Ten test modules already called `reset_provider_health()` by hand; this makes the
    guarantee unconditional instead of leaving it to whoever remembers.
    """
    from core.model_health import reset_provider_health

    reset_provider_health()
    yield
    reset_provider_health()


@pytest.fixture(autouse=True)
def isolation_backend_probe_cache_reset() -> None:
    """Drop `sandbox.job_runner`'s process-wide isolation-backend probe cache between tests.

    `_BACKEND_USABLE` is a module-level dict keyed by backend name, probed once and reused for
    the rest of the process -- a property of the host, not of any one test. Any test that fakes
    backend discovery around a call that reaches `_backend_usable` (mocking `subprocess.run`,
    `shutil.which`, `os.name`, or `sys.platform`) can poison a real backend's cached verdict for
    whatever test the same pytest process runs next. Two independent poisoners were found this
    way, in unrelated files, with no shared markers to grep for: the JobRunner-timeout tests in
    `test_t3_reliability_hardening.py` (mock `subprocess.run` with a `TimeoutExpired` that also
    breaks `_backend_usable`'s own internal probe call) and
    `test_job_runner.py::JobRunnerTests::test_heuristic_only_mode_remains_explicit_opt_in` (fakes
    `shutil.which` to report a fictional Linux `bwrap`, whose real, unmocked probe subprocess
    then fails against that bogus path and caches `bwrap:deny_network=False` as unusable). A
    fixture that individual poisoning tests must remember to opt into is the wrong shape of fix
    for a hazard with no closed set of triggers -- this makes the guarantee a suite-wide
    invariant instead: every test starts AND ends with a clean cache, regardless of what ran
    before or after it. Resetting only at the fixture boundary (never mid-test) leaves normal
    caching behavior *within* one test, positive or negative, completely unaffected.
    """
    from sandbox.job_runner import reset_isolation_backend_probe_cache

    reset_isolation_backend_probe_cache()
    yield
    reset_isolation_backend_probe_cache()


@pytest.fixture(autouse=True)
def request_turn_context_isolation() -> None:
    """Snapshot and restore the request/execution identity ContextVars at the
    test boundary (P1 request-context isolation).

    The A0 request id (``core.semantic.semantic_admissions._CURRENT_REQUEST_ID``)
    and the turn fence tuple (``_EXECUTION_IDENTITY``) are context state, not
    caches: a test that binds them and exits without restoring — the raw
    ``set_request_context(...)`` in test_r5_pipeline_order.py left
    ``req:http:r5-empty-sr`` bound — changes product truth for every later
    turn on the same context, because ``agent.run_once`` binds its own identity
    ONLY when none is present and then fails closed against a foreign id. This
    fixture makes the guarantee a suite-wide invariant instead of leaving it to
    whoever remembers, the same shape as the reset fixtures above: every test
    starts AND ends with the identity family exactly as the process held it.
    The teardown re-installs the PREVIOUS value (never a cleared default), so
    context belonging to an outer scope is restored, not destroyed; within one
    test nothing is touched, so ordered turns keep working.
    """
    from core.semantic import semantic_admissions as _sa

    _prev_request = _sa._CURRENT_REQUEST_ID.get()
    _prev_execution = _sa._EXECUTION_IDENTITY.get()
    yield
    if _sa._CURRENT_REQUEST_ID.get() != _prev_request:
        _sa._CURRENT_REQUEST_ID.set(_prev_request)
    if _sa._EXECUTION_IDENTITY.get() != _prev_execution:
        _sa._EXECUTION_IDENTITY.set(_prev_execution)


@pytest.fixture(autouse=True)
def runtime_storage_reset() -> None:
    configure_default_db_path(_TEST_DB_PATH)
    reset_default_connection()
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    ensure_memory_files()
    reset_runtime_continuity_state()
    # A2 hygiene: production resets semantic admission once per turn; a test is
    # one turn. Without this, a stale admitted record leaks into the next test
    # and A7 finalization would honestly reject it as different content for a
    # foreign logical truth.
    from core.semantic import semantic_result_seam as _seam

    _seam.reset_admission()

    conn = get_connection()
    try:
        # These audit tables deliberately reject DELETE in production. Tests
        # use one shared temporary database, so reset them by dropping the
        # test-only schema; each owning module recreates it on demand.
        for table in IMMUTABLE_RUNTIME_TABLES:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            except Exception:
                continue
        for table in RUNTIME_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
    reset_default_connection()

    memory_path().write_text(MEMORY_TEMPLATE, encoding="utf-8")
    conversation_log_path().write_text("", encoding="utf-8")
    memory_entries_path().write_text("", encoding="utf-8")
    session_summaries_path().write_text("", encoding="utf-8")
    user_heuristics_path().write_text("", encoding="utf-8")
    operator_dense_profile_path().write_text("{}", encoding="utf-8")
    save_preferences(default_preferences())


@pytest.fixture(autouse=True)
def block_live_public_hive_network(monkeypatch):
    """Replace module-level ``urllib.request.urlopen`` so a test that reaches the network by
    accident fails closed for every non-loopback host.

    BOUNDARY OF THIS GUARD, stated honestly: it sees only the ``urlopen`` call shape. Door
    paths that send through an ``OpenerDirector`` instead -- keyed credential verification and
    the connection probe (``no_proxy=True``), and the wallet's origin-pinned fetch
    (``redirect_policy="refuse"``) -- never call module-level ``urlopen`` and pass straight
    through here (measured 2026-09-14: fixture-keyed requests reached api.search.brave.com and
    openrouter.ai through exactly this gap). Cover those with a socket-level seal
    (``egress_guard_plugin.py`` in the autodetection lane's validation logs) or a fake at
    ``OpenerDirector.open``; do not read a green run of a door-path test as proof this guard
    held."""
    real_urlopen = public_hive_client.urllib.request.urlopen

    def guarded_urlopen(request, *args, **kwargs):
        target = getattr(request, "full_url", request)
        host = str(urlsplit(str(target or "")).hostname or "").strip().lower()
        if host in {"", "127.0.0.1", "localhost", "::1"}:
            return real_urlopen(request, *args, **kwargs)
        raise URLError(f"public hive live network blocked under pytest for host '{host or 'unknown'}'")

    monkeypatch.setattr(public_hive_client.urllib.request, "urlopen", guarded_urlopen)


@pytest.fixture(scope="session", autouse=True)
def fence_spawned_daemons_from_live_ollama():
    """The socket policy in `block_live_local_ollama_under_pytest` seals the TEST process. A served
    daemon a test spawns is another process: it inherits only the environment, auto-registers a
    default local provider, lists and inspects the machine's installed models and runs a real
    certification probe against 127.0.0.1:11434 -- which loaded qwen3:0.6b and qwen2.5:7b (12.7 GB)
    into the operator's Ollama during a shard run on 2026-09-07. Every launcher reaches its child
    through `subprocess.Popen`, so the child's Ollama endpoints are pointed at a dead port HERE,
    once, unless the launcher already chose its own (the served rigs point them at their stubs).

    Session-scoped on purpose: a module-scoped rig fixture starts its daemon before any
    function-scoped fixture runs, which is exactly how the first version of this fence was missed.
    """
    if os.environ.get("VOOL_ALPHA_LIVE_SOAK") == "1" or os.environ.get("VOOL_ALLOW_LIVE_OLLAMA_TESTS") == "1":
        yield
        return
    _real_popen = subprocess.Popen
    _dead = "http://127.0.0.1:9"
    _child_endpoints = {
        "OLLAMA_HOST": _dead,
        "VOOL_OLLAMA_URL": _dead,
        "VOOL_RAW_OLLAMA_API_URL": _dead,
        "VOOL_OLLAMA_CHAT_URL": f"{_dead}/api/chat",
        "VOOL_OLLAMA_TAGS_URL": f"{_dead}/api/tags",
        "VOOL_OLLAMA_PS_URL": f"{_dead}/api/ps",
        "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
        "VOOL_SKIP_PROVIDER_PREWARM": "1",
    }

    def _spawns_a_vool_daemon(args: object) -> bool:
        parts = args if isinstance(args, (list, tuple)) else [args]
        joined = " ".join(str(part) for part in parts)
        return "vool_api_server" in joined or "launch_daemon" in joined or "vool_daemon" in joined

    class _FencedPopen(_real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, args, *popen_args, **kwargs):
            if _spawns_a_vool_daemon(args):
                env = dict(kwargs.get("env") if kwargs.get("env") is not None else os.environ)
                for name, value in _child_endpoints.items():
                    env.setdefault(name, value)
                kwargs["env"] = env
            super().__init__(args, *popen_args, **kwargs)

    session_patch = pytest.MonkeyPatch()
    session_patch.setattr(subprocess, "Popen", _FencedPopen)
    try:
        yield
    finally:
        session_patch.undo()


@pytest.fixture(autouse=True)
def block_live_local_ollama_under_pytest(monkeypatch):
    if os.environ.get("VOOL_ALPHA_LIVE_SOAK") == "1" or os.environ.get("VOOL_ALLOW_LIVE_OLLAMA_TESTS") == "1":
        return

    # A developer shell may export the installer's inventory flag. Keep ordinary tests from
    # turning that into an unmocked localhost Ollama probe; tests that need inventory pass an
    # explicit snapshot or opt into the live lane above.
    monkeypatch.setenv("VOOL_REGISTER_INSTALLED_OLLAMA_MODELS", "0")
    # Runtime snapshots intentionally discover the machine's installed/loaded
    # Ollama models. Unit tests need the same code path without depending on a
    # daemon that may or may not be running on the host. Stub the inventory
    # boundary, not production callers; the explicit live-test flags above
    # leave real discovery intact for on-box acceptance tests.
    monkeypatch.setattr(local_ollama_inventory, "_ollama_models_payload", lambda **_kwargs: None)

    def _is_blocked_ollama_target(host: object, port: object) -> bool:
        normalized_host = str(host or "").strip().lower()
        return normalized_host in {"localhost", "127.0.0.1", "::1"} and int(port or 0) == 11434

    def _refuse_live_ollama(_kind: str, address: object) -> None:
        if isinstance(address, tuple) and len(address) >= 2 and _is_blocked_ollama_target(address[0], address[1]):
            raise AssertionError(
                "pytest attempted to reach live local Ollama on 127.0.0.1:11434 without an explicit live-test opt-in"
            )

    # Pushed as a POLICY rather than patched onto the socket directly. The previous version
    # captured `socket.socket.connect` live and delegated to it, which composed correctly -- but
    # only by accident of that one line: capturing a pristine reference instead, the equally
    # natural way to write the same fixture, would have silently discarded any stricter seal
    # installed beneath it, on every test, with nothing to say so. Under `tests/_network_seal` a
    # connection is refused if ANY active policy refuses it, so this fixture can only add
    # prohibitions and can never remove one. It also covers `connect_ex`, which this fixture never
    # patched and which returns an errno rather than raising -- the surface a caller walked through.
    token = network_seal.push("root:no-live-local-ollama", _refuse_live_ollama)
    try:
        yield
    finally:
        network_seal.release(token)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    """Re-apply the network union immediately before every test body.

    This is the one moment in a test's life when "what is installed now" is not a question about
    fixture ordering: every fixture has been set up and nothing else has run. Anything a fixture put
    on a socket surface becomes the union's delegate rather than its replacement, so a test that
    legitimately wraps a socket keeps its wrapper and gains the union above it. Prohibitions can
    only stay equal or become stricter.
    """
    network_seal.reassert()


@pytest.fixture(autouse=True)
def _credential_store_stays_out_of_the_real_keychain(monkeypatch):
    """Pin the credential store to the isolated file-vault for every test.

    The store now prefers the native macOS Keychain when a real ``keyring`` backend is present --
    which it IS on a macOS dev machine -- so an unguarded test calling e.g.
    ``store_credential('llm.cloud.openrouter', ...)`` would write to (and could clobber) the
    developer's real login-Keychain key. Forcing ``VOOL_CREDENTIAL_STORE=vault`` routes every test
    through the file-vault under the per-test ``VOOL_HOME`` instead. The dedicated Keychain tests
    opt back in by monkeypatching ``credential_store._active_keyring`` with an in-memory fake.
    """
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")


@pytest.fixture(autouse=True)
def _blackbox_cas_keys_pinned_to_a_session_keyring(monkeypatch):
    """Pin the Blackbox encrypted-CAS keys to an explicit session key file.

    The CAS (2026-09-02 amendment) resolves its keyring through the canonical secret authority,
    which in a test session routes through the file-vault -- whose key derivation depends on the
    node-signer state other fixtures mutate. That makes key availability order-dependent, and a
    store that cannot resolve keys FAILS CLOSED (every reversible capture refuses), so the suite
    would flake on run order. The explicit key-file channel is the same keyring schema and the
    same crypto; the dedicated authority-path tests (tests/blackbox_coverage) opt out by
    unsetting it and exercising ``resolve_keyring`` directly.
    """
    home = Path(os.environ.get("VOOL_HOME") or tempfile.gettempdir())
    keys_file = home / "blackbox-cas-keys.json"
    if not keys_file.exists():
        from core.blackbox.coverage.cas_keys import CasKeyring

        keys_file.parent.mkdir(parents=True, exist_ok=True)
        keys_file.write_text(CasKeyring.mint().to_json(), encoding="utf-8")
    monkeypatch.setenv("VOOL_BLACKBOX_CAS_KEYS_FILE", str(keys_file))


@pytest.fixture(autouse=True)
def _liquefy_log_projection_stays_out_of_real_data(monkeypatch, tmp_path):
    """Pin the core/liquefy compressed-log projection off and into a per-test dir.

    The projection hook rides on ``BlackboxStore.default_store()``, which many
    existing tests exercise; without this pin every such test would append into
    the developer's real ``data/liquefy_logs``. The lane's own tests opt back in
    with ``monkeypatch.setenv("VOOL_LIQUEFY_LOGS", "1")`` and an explicit home.
    """
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS", "0")
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS_HOME", str(tmp_path / "liquefy_logs"))
    from core.liquefy import hooks as liquefy_hooks

    liquefy_hooks.reset_default_store()
    yield
    liquefy_hooks.reset_default_store()


@pytest.fixture(autouse=True)
def _node_signing_key_stays_out_of_the_real_keychain(monkeypatch):
    """Pin the node signing key to a per-test passphrase, never the real login Keychain.

    The sibling fixture above guarded the credential store and this one did not exist, so the ONLY
    thing standing between the suite and the developer's live node identity was remembering to fake
    ``HOME``. It is not enough: ``keyring.set_password`` is keyed by service/account and ignores
    ``VOOL_HOME`` entirely, so per-test home isolation does not protect that slot.

    What that cost, on 2026-08-02: a full-suite run against a real ``HOME`` provisioned a throwaway
    node seed and ``network/signer.py::_persist_seed`` wrote it straight into ``vool`` /
    ``node_signing_key`` -- the exact slot the live node reads. The keychain item's creation date
    moved to that run and the previous identity was gone; there is no ``.b64`` and no encrypted
    fallback beside the pointer file, because ``_persist_seed`` deletes those once the keyring
    readback verifies. Unrecoverable without an external backup.

    ``VOOL_KEY_STORAGE_MODE=file`` is the load-bearing half: ``_key_storage_preference`` accepts
    only ``{auto, file, keyring}`` and silently returns ``auto`` for anything else, so the mode has
    to be one it actually recognises -- ``file`` is the one that can never reach a keyring backend.
    The passphrase is belt-and-braces: with it set, the ``auto`` branch in ``_persist_seed``
    (``preference == "auto" and passphrase is None and backend is not None``) is already False, so
    even a test that overrides the mode back to ``auto`` still does not touch the OS keyring. Tests
    that genuinely exercise the keyring branch opt back in by monkeypatching the signer's backend
    with an in-memory fake, exactly as the Keychain credential tests do.

    The record PATHS are the third half (2026-09-21 CI finding): ``network.signer`` freezes
    ``_KEY_DIR = data_path("keys")`` at first import -- under the DEFAULT home when no test has set
    ``VOOL_HOME`` yet -- so every test in one pytest process shares ONE keys directory. A test that
    overrides the passphrase in-process (archaeology, signer storage contracts) then RE-SEALS that
    shared record, and the next suite-passphrase reader dies with InvalidTag -- measured as the CI
    demand-ownership "(InvalidTag:)" demand failures and the pollution-matrix child session.
    Repointing the paths into a per-test directory (and resetting the cached keypair) makes each
    test seal its own record with whatever passphrase it uses; the signer contract tests set their
    own paths during the test, after this fixture has run, so they are unaffected.
    """
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "file")
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "test-suite-only-never-a-real-key")
    import network.signer as _signer

    _keys_dir = Path(tempfile.mkdtemp(prefix="vool_pytest_signer_keys_")) / "keys"
    monkeypatch.setattr(_signer, "_KEY_DIR", _keys_dir, raising=False)
    monkeypatch.setattr(_signer, "_LEGACY_PRIV_KEY_PATH", _keys_dir / "node_signing_key.b64", raising=False)
    monkeypatch.setattr(_signer, "_KEY_RECORD_PATH", _keys_dir / "node_signing_key.json", raising=False)
    monkeypatch.setattr(_signer, "_KEYRING_RECORD_PATH", _keys_dir / "node_signing_key.keyring.json", raising=False)
    monkeypatch.setattr(_signer, "_KEY_ARCHIVE_DIR", _keys_dir / "archive", raising=False)
    monkeypatch.setattr(
        _signer, "_ACCOUNT_FILE_PROTECTION_PATH", _keys_dir / "key_storage.passphrase", raising=False
    )
    monkeypatch.setattr(_signer, "_LOCAL_KEYPAIR", None, raising=False)


@pytest.fixture(autouse=True)
def default_test_policy_disables_web_fallback():
    """Force `system.allow_web_fallback` off for every test unless a test
    explicitly opts in via the `enable_web` fixture below.

    The shipped product default (config/default_policy.yaml) is now
    `allow_web_fallback: true` so real users get real DuckDuckGo-backed
    answers out of the box. Tests must stay network-isolated regardless of
    that shipped default, so this autouse fixture pins the test-session
    baseline back to False; `enable_web` (opt-in, per test) flips it back on
    for the specific tests that intentionally exercise live web lookups.
    """
    from core import policy_engine

    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous_cache


@pytest.fixture
def enable_web():
    """Explicitly turn on the opt-in web lookup for a single test.

    Web access is forced off for tests by default (see
    default_test_policy_disables_web_fallback above); tests that exercise the
    live web tools must deliberately enable it here. This flips
    `system.allow_web_fallback` in the cached policy and restores the prior
    cache afterwards.
    """
    from core import policy_engine

    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = True
    system["local_only_mode"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous_cache


@pytest.fixture
def context_result_factory():
    return make_stub_context


@pytest.fixture
def response_normalizer():
    return normalize_response_text


@pytest.fixture
def forbidden_chat_leaks():
    return FORBIDDEN_CHAT_LEAKS


@pytest.fixture
def make_agent(monkeypatch, context_result_factory):
    def factory(*, backend_name: str = "test-backend", device: str = "test-device", persona_id: str = "default") -> VoolAgent:
        agent = VoolAgent(backend_name=backend_name, device=device, persona_id=persona_id)
        monkeypatch.setattr(agent, "_sync_public_presence", lambda *args, **kwargs: None)
        monkeypatch.setattr(agent, "_start_public_presence_heartbeat", lambda *args, **kwargs: None)
        monkeypatch.setattr(agent, "_start_idle_commons_loop", lambda *args, **kwargs: None)
        agent.start()
        agent.context_loader.load = Mock(return_value=context_result_factory())  # type: ignore[assignment]
        return agent

    return factory


# --- plugin lifecycle isolation ---
# The plugin lifecycle store decides which packs are OFFERED. Left at its default it resolves
# under the real data dir, so a test that admits a pack would write into the developer's own
# runtime state and a later test would inherit it. Pinning it per session keeps every admission
# inside the test tree without softening the availability gate itself.
@_pytest.fixture(autouse=True, scope="session")
def _plugin_lifecycle_store(tmp_path_factory):
    import os

    target = tmp_path_factory.mktemp("plugin-lifecycle") / "plugin_lifecycle.json"
    previous = os.environ.get("VOOL_PLUGIN_LIFECYCLE_PATH")
    os.environ["VOOL_PLUGIN_LIFECYCLE_PATH"] = str(target)
    try:
        yield target
    finally:
        if previous is None:
            os.environ.pop("VOOL_PLUGIN_LIFECYCLE_PATH", None)
        else:
            os.environ["VOOL_PLUGIN_LIFECYCLE_PATH"] = previous


@pytest.fixture(autouse=True)
def _fresh_web_engine_memory():
    """`tools.web.web_research` keeps process-global engine memory (per-turn failures and the
    cross-turn cooldown for engines that could not be reached). Tests that simulate transport
    failures must not cool the engines for the tests that follow them."""
    try:
        from tools.web import web_research as _wr

        _wr._reset_engine_memory_for_tests()
    except Exception:
        pass
    yield
    try:
        from tools.web import web_research as _wr

        _wr._reset_engine_memory_for_tests()
    except Exception:
        pass

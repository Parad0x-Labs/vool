"""Turn-context sabotage campaign — the C09/C10/C13 guards are load-bearing.

Each sabotage DISABLES exactly one guard in the physical source (byte-exact
restore in a ``finally``, house mutation pattern per EXPORT_SABOTAGE_MATRIX)
and proves BOTH directions in one test:

    GUARDED  — the named guard refuses the violation (typed raise / honest
               miss / excluded page), and
    SABOTAGED — with the guard's code neutralized, the violation silently
               succeeds (the leak / resurrection / stale hit happens), which
               is exactly the mutation the detector assertions pin.

A guard whose removal changes nothing is not load-bearing; if any sabotage
here does NOT change behavior, the guard it names is dead code and the
product tests that cite it are decorating, not defending.

Sabotage matrix (guard → mutation → detector direction pinned here):

    S1  admission principal scoping (C09)     — _admission_row skips the
        principal comparison → foreign recall_page leaks instead of raising
        AdmissionScopeError.
    S2  frozen-state guard (C09)              — assert_frozen_stable call
        neutralized in compile_turn_context → a SQL-tampered
        turn_context_generations.frozen_hash compiles silently instead of
        raising LayoutLawViolation.
    S3  digest append-only guard (C09)        — assert_digest_append_only
        neutralized in _append_digest_line AND the digest rebuilt without
        history → note_governance_event accepts a non-prefix rewrite.
    S4  erasure pin fence (C13)               — PinActiveError check in
        context_pages.erase_page disabled → pinned bytes erase.
    S5  erasure governance gate (C13)         — retention-class check in
        erase_page loses the governance-actor conjunct → a PERSISTENT page
        erases with no governance actor.
    S6  cache invalidation by policy epoch (C10) — memo_key folds a constant
        epoch → bump_policy_epoch serves stale results as hits.
    S7  cache invalidation by tool version (C10) — memo_key folds an empty
        version → re-registering with version="v2" serves stale v1 hits.
    S8  measured cache-truth stamp (C10)      — _memoized_runtime_result
        discards the memo's hit bool and skips the stamp → a hit reaches
        callers with NO details["cache"] provenance.
    S9  A8 read gate (C13)                    — the WITHHELD/ERASED verdict
        checks in _a8_exclusion disabled → a withheld page compiles into
        block_text.
    S10 A8 write fence (C13)                  — writer_may_persist_text not
        consulted by admit_turn_context → ERASED text is re-admitted and
        persists again (resurrection at the door; the A8 read gate remains a
        second, independent wall at compile).

Run STANDALONE (never in parallel with other suites): the sabotages mutate
core source at runtime and restore byte-exact in ``finally``.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TURN_CONTEXT = "core/turn_context.py"
CONTEXT_PAGES = "core/context_pages.py"
TOOL_MEMO = "core/tool_memo.py"
RUNTIME_TOOLS = "core/runtime_execution_tools.py"


# ── The mutation harness ─────────────────────────────────────────────────────


@contextlib.contextmanager
def source_sabotage(module_name: str, relative_path: str, replacements):
    """Disable one guard by mutating its source file for the duration of the block.

    House law (tests/foundation/test_a8_final_freeze_sabotage.py +
    validation-logs/final-integration-20260903/EXPORT_SABOTAGE_MATRIX.md):
    read the bytes, replace an EXACT UNIQUE snippet (code drift fails the test
    with a clear message BEFORE any byte is mutated), write, reload the module
    so the running process executes the sabotage; the finally block writes the
    ORIGINAL bytes back, reloads again, and asserts byte-exact restoration.
    """
    path = PROJECT_ROOT / relative_path
    original = path.read_bytes()
    sha_before = hashlib.sha256(original).hexdigest()
    # importlib.reload REBUILDS every class the module defines, so the restore
    # below would hand back byte-identical source but brand-new class objects.
    # Any reference captured earlier -- a sibling test module's
    # ``from core.context_pages import PageNotFoundError`` bound at collection,
    # an isinstance check anywhere in the process -- would then be comparing
    # against a class this module no longer raises, and a correct ``raise``
    # would sail straight through ``pytest.raises``. Capture the originals now
    # and put them back after the restore: the source is identical again, so
    # identity is the only thing the reload legitimately changed, and it is
    # exactly what must not leak into the next test.
    original_module = importlib.import_module(module_name)
    original_types = {
        name: obj
        for name, obj in vars(original_module).items()
        if isinstance(obj, type) and getattr(obj, "__module__", "") == module_name
    }
    mutated = original.decode("utf-8")
    for old, new in replacements:
        occurrences = mutated.count(old)
        if occurrences != 1:
            raise AssertionError(
                f"sabotage target drifted in {relative_path}: expected exactly "
                f"1 occurrence, found {occurrences}; refusing to mutate "
                f"blindly. Snippet: {old!r}"
            )
        mutated = mutated.replace(old, new)
    try:
        path.write_text(mutated, encoding="utf-8")
        module = importlib.reload(importlib.import_module(module_name))
        yield module
    finally:
        path.write_bytes(original)
        restored_module = importlib.reload(importlib.import_module(module_name))
        for name, obj in original_types.items():
            setattr(restored_module, name, obj)
    restored = path.read_bytes()
    assert hashlib.sha256(restored).hexdigest() == sha_before, (
        f"byte-exact restore failed for {relative_path}: the tree would be "
        "left dirty — this is a harness bug, never acceptable"
    )


# ── Hermetic fixture (isolated-state law, verbatim rig) ──────────────────────


@pytest.fixture()
def tc_env(tmp_path, monkeypatch):
    """Per-test VOOL_HOME + SQLite, the operator_profile_rig isolated state."""
    from core import finalization, tool_memo
    from tests.operator_profile_rig import profile_env_generator

    finalization.reset_governance_readiness_for_tests()
    # These sabotage tests register probe tools into the PROCESS-GLOBAL purity
    # table (that is the code under test). Without a snapshot/restore the
    # registration leaks into every later test in the same shard: a repartition
    # that co-located this file with tests/test_tool_memo_cache_truth.py made
    # its pristine-set assertion fail on the leaked 'sab7.probe' (measured
    # 2026-09-26: run 36245722295 shard 9; reproduced on unchanged main by
    # running the two files in one process).
    pure_tools_snapshot = dict(tool_memo._PURE_TOOLS)
    yield from profile_env_generator(tmp_path, monkeypatch)
    tool_memo._PURE_TOOLS.clear()
    tool_memo._PURE_TOOLS.update(pure_tools_snapshot)
    finalization.reset_governance_readiness_for_tests()


# ── Shared helpers ───────────────────────────────────────────────────────────


def _digest_text(principal: str, session_id: str) -> str:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT digest_text FROM turn_context_generations "
            "WHERE principal = ? AND session_id = ?",
            (principal, session_id),
        ).fetchone()
    finally:
        conn.close()
    return str(row["digest_text"]) if row else ""


def _tamper_frozen_hash(principal: str, session_id: str, value: str) -> None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE turn_context_generations SET frozen_hash = ? "
            "WHERE principal = ? AND session_id = ?",
            (value, principal, session_id),
        )
        conn.commit()
        assert cursor.rowcount == 1, "generation row missing — fixture drifted"
    finally:
        conn.close()


def _frozen_hash_row(principal: str, session_id: str) -> str:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT frozen_hash FROM turn_context_generations "
            "WHERE principal = ? AND session_id = ?",
            (principal, session_id),
        ).fetchone()
    finally:
        conn.close()
    return str(row["frozen_hash"]) if row else ""


# ── S1 — admission scoping (C09): the principal comparison ───────────────────


def test_s1_sabotaged_principal_scope_leaks_foreign_recall(tc_env):
    import core.turn_context as tc

    secret = "SCOPE-SECRET-PAGE-401 owner's private observation"
    scope = tc.TurnScope(principal="owner_local", session_id="sess-s1")
    admitted = tc.admit_turn_context(secret, scope=scope, origin="explicit")

    # GUARDED: another principal's recall is refused at the door.
    with pytest.raises(tc.AdmissionScopeError):
        tc.recall_page("attacker", admitted["admission_id"])
    # and the owner's own recall still works (the guard is not a wall).
    owner_view = tc.recall_page("owner_local", admitted["admission_id"])
    assert owner_view["content"] == secret

    old = '    if str(record["principal"]) != principal:'
    new = "    if False:  # SABOTAGE: principal scope comparison disabled"
    with source_sabotage("core.turn_context", TURN_CONTEXT, [(old, new)]):
        # SABOTAGED: the guard was the ONLY refusal — foreign recall leaks.
        leaked = tc.recall_page("attacker", admitted["admission_id"])
        assert leaked["content"] == secret, (
            "sabotage failed: something besides the principal comparison "
            "still refuses foreign recall"
        )

    # RESTORED: scoped refusal is back.
    with pytest.raises(tc.AdmissionScopeError):
        tc.recall_page("attacker", admitted["admission_id"])


# ── S2 — frozen-state guard (C09): assert_frozen_stable on compile ───────────


def test_s2_sabotaged_frozen_guard_silently_accepts_tampered_frozen_hash(tc_env):
    import core.turn_context as tc
    from core.context_layout import LayoutLawViolation

    principal, session = "owner_local", "sess-s2"
    scope = tc.TurnScope(principal=principal, session_id=session, turn_id="turn-s2")
    tc.admit_turn_context(
        "FROZEN-PAGE-402 pinned evidence the frozen guard protects",
        scope=scope,
    )
    # First compile writes the generation row; a second stable compile passes.
    tc.compile_turn_context(principal, session)
    tc.compile_turn_context(principal, session)

    # Tamper the frozen header hash OUTSIDE the authority (raw SQL).
    _tamper_frozen_hash(principal, session, "tampered-frozen-deadbeef")

    # GUARDED: compiling over a mutated frozen zone raises loudly, never serves.
    with pytest.raises(LayoutLawViolation):
        tc.compile_turn_context(principal, session)

    old = "        assert_frozen_stable("
    new = "        (lambda *a, **k: None)(  # SABOTAGE: frozen-zone guard disabled"
    with source_sabotage("core.turn_context", TURN_CONTEXT, [(old, new)]):
        # SABOTAGED: the tampered generation compiles silently and the tamper
        # is quietly overwritten — the guard was the only witness.
        compilation = tc.compile_turn_context(principal, session)
        assert len(compilation.items) == 1, (
            "sabotage failed: the tampered frozen hash still blocked compile"
        )
        expected = hashlib.sha256(
            tc.frozen_header(principal, session).encode("utf-8")
        ).hexdigest()
        assert _frozen_hash_row(principal, session) == expected, (
            "sabotage failed: the tampered frozen hash was not silently accepted"
        )

    # RESTORED: a fresh tamper raises again...
    _tamper_frozen_hash(principal, session, "tampered-frozen-again")
    with pytest.raises(LayoutLawViolation):
        tc.compile_turn_context(principal, session)
    # ...and the LEGAL escape — bump_context_generation — heals it.
    tc.bump_context_generation(principal, session, reason="sabotage drill")
    healed = tc.compile_turn_context(principal, session)
    assert healed.generation == 2
    assert len(healed.items) == 1


# ── S3 — digest append-only guard (C09): assert_digest_append_only ───────────


def test_s3_sabotaged_digest_guard_accepts_history_rewrite(tc_env):
    import core.turn_context as tc
    from core.context_layout import LayoutLawViolation, assert_digest_append_only

    # GUARDED, directly: a digest that does not carry the old bytes forward
    # as a verbatim prefix is a rewrite, and the layout law refuses it.
    with pytest.raises(LayoutLawViolation):
        assert_digest_append_only({}, "old-line\n", "rewritten\n")

    principal, session = "owner_local", "sess-s3"
    tc.note_governance_event(principal, session, event="probe_one", admission_id="aaa")
    tc.note_governance_event(principal, session, event="probe_two", admission_id="bbb")
    seeded = _digest_text(principal, session)
    assert "probe_one" in seeded and "probe_two" in seeded

    # SABOTAGE: the guard call is neutralized AND the digest is rebuilt from
    # the new line alone — note_governance_event becomes a rewrite engine.
    old = "    assert_digest_append_only({}, previous, new_digest)"
    new = (
        "    new_digest = f\"{line} @ {stamp}\\n\""
        "  # SABOTAGE: digest rebuilt, history dropped\n"
        "    (lambda *a, **k: None)({}, previous, new_digest)"
    )
    with source_sabotage("core.turn_context", TURN_CONTEXT, [(old, new)]):
        # SABOTAGED: the rewrite is ACCEPTED — the whole prior history vanishes.
        tc.note_governance_event(principal, session, event="probe_three", admission_id="ccc")
        rewritten = _digest_text(principal, session)
        assert "probe_three" in rewritten
        assert "probe_one" not in rewritten and "probe_two" not in rewritten, (
            "sabotage failed: history survived a rewrite the guard should "
            "have refused"
        )

    # RESTORED: appending again — and what exists now survives byte-for-byte.
    tc.note_governance_event(principal, session, event="probe_four", admission_id="ddd")
    final_digest = _digest_text(principal, session)
    assert "probe_three" in final_digest and "probe_four" in final_digest
    assert final_digest.startswith(rewritten), (
        "restore failed: the digest is still being rewritten, not appended"
    )


# ── S4 — erasure pin fence (C13): PinActiveError in erase_page ───────────────


def test_s4_sabotaged_pin_fence_erases_pinned_bytes(tc_env):
    import core.context_pages as cp

    page = cp.ContextPage(
        kind="tool_result",
        content='{"sab": "pinned-404", "obligation": "open"}',
        source="sabotage.probe",
        session_id="sess-s4",
        task_id="turn-s4",
    )
    admitted = cp.admit_page(page)
    page_hash = admitted["page_hash"]
    assert cp._active_pin_count(page_hash) > 0

    # GUARDED: the governed path REACHES the pin fence (the actor satisfies the
    # retention gate), and the fence refuses while the obligation is open.
    with pytest.raises(cp.PinActiveError):
        cp.erase_page(page_hash, governance_actor="operator", governance_reason="drill")
    assert cp.page_in(page_hash) == page.content

    old = "    if _active_pin_count(page_hash) > 0:\n        raise PinActiveError(page_hash)"
    new = (
        "    if False:  # SABOTAGE: pin fence disabled\n"
        "        raise PinActiveError(page_hash)"
    )
    with source_sabotage("core.context_pages", CONTEXT_PAGES, [(old, new)]):
        # SABOTAGED: open obligations no longer hold their page — bytes erased.
        cp.erase_page(page_hash, governance_actor="operator", governance_reason="drill")
        with pytest.raises(cp.PageNotFoundError):
            cp.page_in(page_hash)

    # RESTORED: a fresh pinned page is refused again.
    page2 = cp.ContextPage(
        kind="tool_result",
        content='{"sab": "pinned-404-restore", "obligation": "open"}',
        source="sabotage.probe",
        session_id="sess-s4",
        task_id="turn-s4-restore",
    )
    admitted2 = cp.admit_page(page2)
    with pytest.raises(cp.PinActiveError):
        cp.erase_page(admitted2["page_hash"], governance_actor="operator", governance_reason="drill")
    assert cp.page_in(admitted2["page_hash"]) == page2.content


# ── S5 — erasure governance gate (C13): ErasureNotAllowedError ───────────────


def test_s5_sabotaged_governance_gate_erases_persistent_bytes_ungoverned(tc_env):
    import core.context_pages as cp

    page = cp.ContextPage(
        kind="tool_result",
        content='{"sab": "persistent-405", "keep": true}',
        source="sabotage.probe",
        session_id="sess-s5",
    )
    admitted = cp.admit_page(page)
    page_hash = admitted["page_hash"]

    # GUARDED: a PERSISTENT page refuses ungoverned erasure; bytes survive.
    with pytest.raises(cp.ErasureNotAllowedError):
        cp.erase_page(page_hash)
    assert cp.page_in(page_hash) == page.content

    # SABOTAGE: the retention/governance gate is disabled outright — the
    # instructed mid-condition narrowing (`!= "ERASABLE"`) would only make the
    # check STRICTER (raise even for governed erasures), so the leak requires
    # the whole condition neutralized.
    old = '    if row["retention_class"] != "ERASABLE" and not governance_actor.strip():'
    new = "    if False:  # SABOTAGE: governance gate disabled"
    with source_sabotage("core.context_pages", CONTEXT_PAGES, [(old, new)]):
        # SABOTAGED: no actor, no reason — the persistent bytes erase anyway.
        cp.erase_page(page_hash)
        with pytest.raises(cp.PageNotFoundError):
            cp.page_in(page_hash)

    # RESTORED: ungoverned erasure of a persistent page is refused again.
    page2 = cp.ContextPage(
        kind="tool_result",
        content='{"sab": "persistent-405-restore", "keep": true}',
        source="sabotage.probe",
        session_id="sess-s5",
    )
    admitted2 = cp.admit_page(page2)
    with pytest.raises(cp.ErasureNotAllowedError):
        cp.erase_page(admitted2["page_hash"])
    assert cp.page_in(admitted2["page_hash"]) == page2.content


# ── S6 — cache invalidation by policy epoch (C10): memo_key folding ──────────


def test_s6_sabotaged_epoch_folding_serves_stale_results_across_policy_bump(tc_env):
    import uuid

    import core.tool_memo as tm

    calls: list[int] = []

    def fn():
        calls.append(1)
        return {"execution": len(calls)}

    # GUARDED: miss → hit; after bump_policy_epoch the SAME args are an
    # honest miss (a result computed under an old policy never serves a new one).
    name = f"sab6.guarded.{uuid.uuid4().hex}"
    tm.register_pure_tool(name)
    _, miss = tm.memoize(name, {}, fn)
    assert miss is False
    _, hit = tm.memoize(name, {}, fn)
    assert hit is True and len(calls) == 1
    key_before = tm.memo_key(name, {})
    tm.bump_policy_epoch("sabotage drill")
    key_after = tm.memo_key(name, {})
    assert key_before != key_after, "policy bump did not move the memo key"
    _, post_bump = tm.memoize(name, {}, fn)
    assert post_bump is False and len(calls) == 2, (
        "stale pre-bump result served under the new policy with the guard live"
    )

    # SABOTAGE: memo_key folds a constant epoch — keys cannot move.
    old = '"epoch": int(epoch),'
    new = '"epoch": 0,  # SABOTAGE: policy epoch ignored'
    with source_sabotage("core.tool_memo", TOOL_MEMO, [(old, new)]):
        name = f"sab6.sabotaged.{uuid.uuid4().hex}"
        tm.register_pure_tool(name)
        stale_key_a = tm.memo_key(name, {})
        seeded, seeded_hit = tm.memoize(name, {}, fn)
        assert seeded_hit is False
        seeded_calls = len(calls)
        tm.bump_policy_epoch("sabotage window bump")
        stale_key_b = tm.memo_key(name, {})
        assert stale_key_a == stale_key_b, (
            "sabotage failed: keys still move — the mutation did not take"
        )
        stale, stale_hit = tm.memoize(name, {}, fn)
        # SABOTAGED: the pre-bump payload is served under the NEW policy.
        assert stale_hit is True, (
            "sabotage failed: the epoch still invalidates — guard not load-bearing?"
        )
        assert stale == seeded
        assert len(calls) == seeded_calls, (
            "sabotage failed: the tool re-executed — no stale result was served"
        )

    # RESTORED: the epoch bump invalidates again.
    name = f"sab6.restored.{uuid.uuid4().hex}"
    tm.register_pure_tool(name)
    restored_calls: list[int] = []

    def restored_fn():
        restored_calls.append(1)
        return {"execution": len(restored_calls)}

    _, first_hit = tm.memoize(name, {"phase": "restored"}, restored_fn)
    assert first_hit is False
    _, warm = tm.memoize(name, {"phase": "restored"}, restored_fn)
    assert warm is True and len(restored_calls) == 1
    tm.bump_policy_epoch("restored drill")
    _, post = tm.memoize(name, {"phase": "restored"}, restored_fn)
    assert post is False and len(restored_calls) == 2, (
        "restore failed: epoch bump still does not invalidate"
    )


# ── S7 — cache invalidation by tool version (C10): memo_key folding ──────────


def test_s7_sabotaged_version_folding_serves_stale_results_across_reregistration(tc_env):
    import core.tool_memo as tm

    name = "sab7.probe"
    calls: list[int] = []

    def fn():
        calls.append(1)
        return {"schema": len(calls)}

    # GUARDED: re-registering with a NEW version invalidates by construction —
    # the key changes, so the next lookup is an honest miss.
    tm.register_pure_tool(name, version="v1")
    _, miss = tm.memoize(name, {}, fn)
    assert miss is False
    tm.register_pure_tool(name, version="v2")
    _, post_version = tm.memoize(name, {}, fn)
    assert post_version is False and len(calls) == 2, (
        "stale v1 result served after a v2 re-registration with the guard live"
    )

    # SABOTAGE: memo_key folds an empty version — re-registration is invisible.
    old = '"version": str(resolved_version),'
    new = '"version": "",  # SABOTAGE: result version ignored'
    with source_sabotage("core.tool_memo", TOOL_MEMO, [(old, new)]):
        tm.register_pure_tool(name, version="v1")
        seeded, seeded_hit = tm.memoize(name, {}, fn)
        assert seeded_hit is False
        tm.register_pure_tool(name, version="v2")
        stale, stale_hit = tm.memoize(name, {}, fn)
        # SABOTAGED: the stale v1 payload is served as if it were v2.
        assert stale_hit is True, (
            "sabotage failed: the version still invalidates — guard not load-bearing?"
        )
        assert stale == seeded

    # RESTORED: version re-registration invalidates again.
    tm.register_pure_tool(name, version="v1")
    restored_calls: list[int] = []

    def restored_fn():
        restored_calls.append(1)
        return {"schema": len(restored_calls)}

    _, warm_up = tm.memoize(name, {"phase": "restored"}, restored_fn)
    assert warm_up is False
    tm.register_pure_tool(name, version="v2")
    _, post = tm.memoize(name, {"phase": "restored"}, restored_fn)
    assert post is False and len(restored_calls) == 2, (
        "restore failed: version re-registration still does not invalidate"
    )


# ── S8 — measured cache truth (C10): the stamp in _memoized_runtime_result ───


def test_s8_sabotaged_hit_stamp_drops_measured_cache_truth(tc_env, monkeypatch):
    import core.runtime_execution_tools as ret
    import core.tool_memo as tm

    monkeypatch.setattr(tm, "_PURE_TOOLS", {})

    def _produce(ret_module, marker):
        return ret_module.RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="ok",
            response_text=marker,
            details={},
        )

    # GUARDED: the hit/miss verdict is MEASURED and stamped — the second call
    # reports details["cache"]["cache_hit"] True and serves the stored payload.
    guarded_name = "sab8.probe.guarded"
    tm.register_pure_tool(guarded_name)
    first = ret._memoized_runtime_result(
        guarded_name, {}, lambda: _produce(ret, "first-execution")
    )
    assert first.details["cache"]["cache_hit"] is False
    assert first.details["cache"]["measured"] is True
    second = ret._memoized_runtime_result(
        guarded_name, {}, lambda: _produce(ret, "second-execution")
    )
    assert second.details["cache"]["cache_hit"] is True
    assert second.response_text == "first-execution", "hit did not serve the stored payload"

    # SABOTAGE: the hit bool is discarded again and the stamp block is gone.
    old1 = "    payload, cache_hit = memoize(intent, arguments, _produce_json, scope=scope)"
    new1 = "    payload, _hit = memoize(intent, arguments, _produce_json, scope=scope)"
    old2 = (
        "    cache_truth = {\n"
        '        "cache_hit": bool(cache_hit),\n'
        '        "measured": True,\n'
        '        "memo_tool": intent,\n'
        '        "policy_epoch": memo_policy_epoch(),\n'
        "    }\n"
        "    details = dict(result.details or {})\n"
        '    details["cache"] = cache_truth\n'
        "    observation = details.get(\"observation\")\n"
        "    if isinstance(observation, dict):\n"
        "        merged_observation = dict(observation)\n"
        '        merged_observation["cache"] = dict(cache_truth)\n'
        '        details["observation"] = merged_observation\n'
        "    result.details = details\n"
        "    return result"
    )
    new2 = "    return result"
    with source_sabotage(
        "core.runtime_execution_tools", RUNTIME_TOOLS, [(old1, new1), (old2, new2)]
    ):
        sabotaged_name = "sab8.probe.sabotaged"
        tm.register_pure_tool(sabotaged_name)
        ret._memoized_runtime_result(
            sabotaged_name, {}, lambda: _produce(ret, "seeded")
        )
        served = ret._memoized_runtime_result(
            sabotaged_name, {}, lambda: _produce(ret, "fresh")
        )
        # SABOTAGED: the memo STILL serves the hit — but with NO measured
        # truth attached. The test that pins measured cache truth goes red.
        assert served.response_text == "seeded", (
            "sabotage failed: the memo stopped serving hits entirely"
        )
        assert "cache" not in served.details, (
            "sabotage failed: the cache-truth stamp survived the mutation"
        )

    # RESTORED: measured truth is stamped again.
    restored_name = "sab8.probe.restored"
    tm.register_pure_tool(restored_name)
    warm = ret._memoized_runtime_result(
        restored_name, {}, lambda: _produce(ret, "restored-seed")
    )
    assert warm.details["cache"]["cache_hit"] is False
    hit = ret._memoized_runtime_result(
        restored_name, {}, lambda: _produce(ret, "restored-fresh")
    )
    assert hit.details["cache"]["cache_hit"] is True
    assert hit.details["cache"]["measured"] is True
    assert hit.response_text == "restored-seed"


# ── S9 — A8 read gate (C13): _a8_exclusion verdict checks ────────────────────


def test_s9_sabotaged_a8_read_gate_compiles_withheld_page(tc_env):
    import core.turn_context as tc
    from tests.operator_profile_rig import admit_finalize, withhold

    text = "WITHHOLD-PAGE-409 classified observation bytes"
    commit = admit_finalize(text, request_id="req-sab-9")
    scope = tc.TurnScope(principal="owner_local", session_id="sess-s9")
    admitted = tc.admit_turn_context(
        text, scope=scope, request_id="req-sab-9", origin="explicit"
    )
    withhold(commit["finalization_id"])

    # GUARDED: the withheld page is EXCLUDED from the compiled block, named,
    # and recall refuses it — every page re-passes the A8 gates at read time.
    compilation = tc.compile_turn_context(
        "owner_local", "sess-s9", query_text="classified observation"
    )
    reasons = [str(entry["reason"]) for entry in compilation.exclusions]
    assert any(reason.startswith("a8_availability_") for reason in reasons), (
        f"guarded run did not name an A8 availability exclusion: {reasons}"
    )
    assert text not in compilation.block_text
    with pytest.raises(tc.AdmissionClosedError):
        tc.recall_page("owner_local", admitted["admission_id"])

    # SABOTAGE: the verdict checks are disabled — _a8_exclusion always says None.
    old_erased = '    if "ERASED" in verdicts:'
    new_erased = "    if False:  # SABOTAGE: ERASED verdict ignored"
    old_withheld = '    if "WITHHELD" in verdicts:'
    new_withheld = "    if False:  # SABOTAGE: WITHHELD verdict ignored"
    with source_sabotage(
        "core.turn_context",
        TURN_CONTEXT,
        [(old_erased, new_erased), (old_withheld, new_withheld)],
    ):
        # SABOTAGED: the withheld page COMPILES into the served block.
        leaked = tc.compile_turn_context(
            "owner_local", "sess-s9", query_text="classified observation"
        )
        assert text in leaked.block_text, (
            "sabotage failed: the withheld page is still excluded — another "
            "gate is holding"
        )
        leaked_recall = tc.recall_page("owner_local", admitted["admission_id"])
        assert leaked_recall["content"] == text

    # RESTORED: the read gate excludes (and recall refuses) again.
    recompiled = tc.compile_turn_context(
        "owner_local", "sess-s9", query_text="classified observation"
    )
    assert text not in recompiled.block_text
    assert any(
        str(entry["reason"]).startswith("a8_availability_")
        for entry in recompiled.exclusions
    )
    with pytest.raises(tc.AdmissionClosedError):
        tc.recall_page("owner_local", admitted["admission_id"])


# ── S10 — A8 write fence (C13): writer_may_persist_text at admission ─────────


def test_s10_sabotaged_write_fence_resurrects_erased_text(tc_env):
    import core.turn_context as tc
    from core.finalization import writer_may_persist_text
    from tests.operator_profile_rig import admit_finalize, erase

    text = "ERASE-PAGE-410 forgotten observation bytes"
    commit = admit_finalize(text, request_id="req-sab-10")
    erase(commit["finalization_id"])
    # Setup law: the fence holds the erasure tombstone before anything admits.
    assert writer_may_persist_text(text) is False

    scope = tc.TurnScope(principal="owner_local", session_id="sess-s10", turn_id="turn-s10")

    # GUARDED: admitting the erased text is refused at the door — typed,
    # and stored nowhere.
    with pytest.raises(tc.AdmissionRefusedError):
        tc.admit_turn_context(text, scope=scope)

    # SABOTAGE: the fence is not consulted at admission.
    old = "    if not writer_may_persist_text(text):"
    new = "    if False:  # SABOTAGE: write fence disabled"
    with source_sabotage("core.turn_context", TURN_CONTEXT, [(old, new)]):
        from core.context_pages import page_in

        # SABOTAGED: the admission SUCCEEDS — erased bytes persist again.
        resurrected = tc.admit_turn_context(text, scope=scope)
        assert resurrected["admission_id"], (
            "sabotage failed: the write fence still refused at the door"
        )
        assert page_in(resurrected["page_hash"]) == text, (
            "sabotage failed: the erased text did not reach the store"
        )
        # Defense in depth, pinned while we are here: the A8 READ gate is a
        # SECOND, independent wall — the resurrected admission compiles to an
        # availability exclusion, never into the served block.
        compiled = tc.compile_turn_context(
            "owner_local", "sess-s10", query_text="forgotten observation"
        )
        assert text not in compiled.block_text
        assert any(
            str(entry["reason"]).startswith("a8_availability_")
            for entry in compiled.exclusions
        )

    # RESTORED: the fence refuses again (the tombstone outlives the sabotage).
    scope_restore = tc.TurnScope(
        principal="owner_local", session_id="sess-s10", turn_id="turn-s10-restore"
    )
    with pytest.raises(tc.AdmissionRefusedError):
        tc.admit_turn_context(text, scope=scope_restore)

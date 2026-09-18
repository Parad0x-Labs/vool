"""C10 — the MEASURED tool-memo truth that reaches callers, and its invalidation axes.

Companion to ``test_tool_memo.py`` (the fail-closed purity gate, TTL expiry,
canonical keys) and ``test_tool_memo_dispatch.py`` (typed hits at the dispatch
door): this suite pins what those two do not — the hit/miss verdict as it is
MEASURED at the only seam that mechanically knows it, stamped into
``details["cache"]`` by ``_memoized_runtime_result``, receipted in
``tool_memo.memo_stats``, and the invalidation axes the earlier suites leave
uncovered:

    POLICY   — ``bump_policy_epoch`` re-keys EVERY entry (an honest miss under
               the new epoch) and receipts the bump to the append-only
               ``event_hash_chain``.
    SCOPE    — a different workspace root is a different key: one workspace's
               memo can never answer for another.
    VERSION  — re-registering a tool under a new ``version`` re-keys its
               entries; restoring the original version re-reaches the original
               key (the row was orphaned, not lost).

``invalidate_tool`` (pinned last) is a DIFFERENT mechanism, and the difference
is measured here: epoch/version invalidation only makes entries UNREACHABLE —
the old bytes stay in ``tool_memo_cache`` and ``memo_stats`` entries keep
counting them — while ``invalidate_tool`` physically DELETEs the rows.

Provider honesty is pinned too: the measured stamp carries exactly
``cache_hit/measured/memo_tool/policy_epoch`` and never a provider claim, and
across ``core/tool_memo.py`` + ``core/turn_context.py`` the phrase
``provider_cache`` exists only as the turn-context receipt's honest
``"unmeasured"`` constant — never a True verdict.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import core.tool_memo as tool_memo
from core.tool_memo import memo_stats, memoize, policy_epoch, register_pure_tool
from storage.db import get_connection
from tests.operator_profile_rig import profile_env_generator

# The ONLY purity registrations in the runtime (greppable by law at
# core/runtime_execution_tools.py import time).
_RUNTIME_REGISTERED_TOOLS = ("workspace.identity", "machine.inspect_specs")


# ── Hermetic fixture: isolated VOOL_HOME + per-test SQLite, exact counts ────


@pytest.fixture()
def memo_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _exact_memo_counts(memo_env):
    """Start every test from an EMPTY memo table so hit/entry counts are exact."""
    tool_memo._init_table()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM tool_memo_cache")
        conn.commit()
    finally:
        conn.close()
    yield


def _workspace(env: dict, name: str) -> Path:
    ws = Path(env["home"]).parent / name
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _identity(env: dict, ws: Path):
    """The dispatch door the way production calls it: workspace_root travels in
    source_context — that is what _dispatch_runtime_tool actually reads."""
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(
        "workspace.identity",
        {},
        source_context={"surface": "test", "workspace_root": str(ws)},
    )


# ── 1. Hit truth reaches the dispatcher ──────────────────────────────────────


def test_hit_truth_reaches_the_dispatcher(memo_env):
    from core.runtime_execution_tools import RuntimeExecutionResult

    ws = _workspace(memo_env, "ws-truth")
    first = _identity(memo_env, ws)
    assert type(first) is RuntimeExecutionResult
    miss_stamp = first.details["cache"]
    assert miss_stamp["cache_hit"] is False
    assert miss_stamp["measured"] is True
    assert miss_stamp["memo_tool"] == "workspace.identity"
    assert miss_stamp["policy_epoch"] == policy_epoch()
    assert first.details["observation"]["cache"]["cache_hit"] is False

    second = _identity(memo_env, ws)
    # THE TYPE LAW: a hit is the same typed result as a miss — reconstructed
    # from its stored dict shape, with the truth attached AFTER reconstruction.
    assert type(second) is RuntimeExecutionResult, "a memo hit must be typed, not a raw dict"
    hit_stamp = second.details["cache"]
    assert hit_stamp["cache_hit"] is True
    assert hit_stamp["measured"] is True
    assert hit_stamp["memo_tool"] == "workspace.identity"
    assert hit_stamp["policy_epoch"] == policy_epoch()
    assert second.details["observation"]["cache"]["cache_hit"] is True
    # the served payload is the miss's stored answer, verbatim
    assert second.response_text == first.response_text


# ── 2. The stamp agrees with the receipted counter ───────────────────────────


def test_measured_accounting_agrees_with_the_stamped_truth(memo_env):
    ws = _workspace(memo_env, "ws-accounting")
    _identity(memo_env, ws)  # miss
    assert memo_stats("workspace.identity") == {"entries": 1, "hits": 0}
    _identity(memo_env, ws)  # hit
    # the hit the caller was TOLD about is the hit the table COUNTED
    assert memo_stats("workspace.identity") == {"entries": 1, "hits": 1}


# ── 3. Policy invalidation (epoch) + its chain receipt ───────────────────────


def test_policy_epoch_bump_is_a_measured_miss_receipted_to_the_chain(memo_env):
    from storage.event_hash_chain import verify_chain

    ws = _workspace(memo_env, "ws-policy")
    _identity(memo_env, ws)  # miss
    _identity(memo_env, ws)  # hit
    epoch_before = policy_epoch()

    new_epoch = tool_memo.bump_policy_epoch("test reason")
    assert new_epoch == epoch_before + 1

    third = _identity(memo_env, ws)
    stamp = third.details["cache"]
    # same tool, same args, same scope — a policy bump alone must turn the hit
    # into an honest MISS stamped with the NEW epoch
    assert stamp["cache_hit"] is False
    assert stamp["policy_epoch"] == new_epoch

    # the bump is an auditable event on the append-only hash chain
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT event_id, payload_json FROM event_hash_chain "
            "WHERE event_id LIKE 'tool-memo-policy-epoch:%' ORDER BY seq ASC"
        ).fetchall()
    finally:
        conn.close()
    matching = [row for row in rows if row["event_id"] == f"tool-memo-policy-epoch:{new_epoch}"]
    assert matching, f"epoch {new_epoch} bump was not receipted to the event hash chain"
    payload = json.loads(str(matching[-1]["payload_json"]))
    assert payload["event"] == "tool_memo.policy_epoch_bumped"
    assert payload["epoch"] == new_epoch
    assert payload["reason"] == "test reason"
    assert verify_chain() is True

    # EPOCH invalidation orphans entries, it does not remove them: both rows
    # (old epoch + new epoch) stay in the measured accounting.
    assert memo_stats("workspace.identity") == {"entries": 2, "hits": 1}


# ── 4. Scope invalidation ────────────────────────────────────────────────────


def test_scope_change_is_a_measured_miss_then_a_hit_on_repeat(memo_env):
    ws_a = _workspace(memo_env, "ws-scope-a")
    ws_b = _workspace(memo_env, "ws-scope-b")

    first = _identity(memo_env, ws_a)
    assert first.details["cache"]["cache_hit"] is False
    repeat_a = _identity(memo_env, ws_a)
    assert repeat_a.details["cache"]["cache_hit"] is True

    # same arguments, different workspace root (scope component): A's memo may
    # never answer for B — and the verdict the caller sees says MISS, not a
    # silent substitution.
    foreign = _identity(memo_env, ws_b)
    assert foreign.details["cache"]["cache_hit"] is False
    assert foreign.response_text != repeat_a.response_text

    repeat_b = _identity(memo_env, ws_b)
    assert repeat_b.details["cache"]["cache_hit"] is True
    assert repeat_b.details["cache"]["memo_tool"] == "workspace.identity"
    # two scopes → two distinct entries, each hit exactly once
    assert memo_stats("workspace.identity") == {"entries": 2, "hits": 2}


# ── 5. Version invalidation (with registration snapshot/restore) ─────────────


def test_version_reregistration_invalidates_and_restore_re_reaches_the_original_key(memo_env):
    ws = _workspace(memo_env, "ws-version")
    original = tool_memo._PURE_TOOLS.get("workspace.identity")
    assert original is not None, "workspace.identity must be registered by runtime_execution_tools"
    try:
        _identity(memo_env, ws)  # miss (v1 key)
        _identity(memo_env, ws)  # hit (v1 key)

        # re-register at the same TTL but a NEW result-schema version: every
        # key for this tool changes by construction
        register_pure_tool("workspace.identity", ttl_seconds=original[0], version="v2")
        after_bump = _identity(memo_env, ws)
        assert after_bump.details["cache"]["cache_hit"] is False, (
            "a v1 memo was served after re-registration at version v2"
        )
        assert after_bump.details["cache"]["memo_tool"] == "workspace.identity"
    finally:
        # restore the ORIGINAL registration exactly (ttl + version)
        register_pure_tool("workspace.identity", ttl_seconds=original[0], version=original[1])
    assert tool_memo._PURE_TOOLS["workspace.identity"] == original

    # the v1 row was orphaned by the version bump, not destroyed: with the
    # original registration back, the ORIGINAL key serves a hit again
    restored = _identity(memo_env, ws)
    assert restored.details["cache"]["cache_hit"] is True
    # both rows remain in the table — version invalidation is unreachability,
    # not removal
    assert memo_stats("workspace.identity") == {"entries": 2, "hits": 2}


# ── 6. Unregistered tools carry NO cache claim ───────────────────────────────


def test_unregistered_tools_have_no_memo_row_and_no_cache_stamp(memo_env):
    from core.runtime_execution_tools import execute_runtime_tool

    # memo level: the fail-closed gate executes fresh and records NOTHING —
    # no row, so no receipted hits can ever exist for an unregistered tool
    executions: list[int] = []

    def fresh() -> dict:
        executions.append(1)
        return {"served": "live"}

    result, cache_hit = memoize("not.a.registered.tool", {}, fresh)
    assert cache_hit is False
    assert result == {"served": "live"}
    assert len(executions) == 1
    assert memo_stats("not.a.registered.tool") == {"entries": 0, "hits": 0}

    # dispatcher level: a read tool whose result HAS details carries no
    # "cache" key anywhere, because unregistered intents never reach
    # _memoized_runtime_result
    ws = _workspace(memo_env, "ws-unregistered")
    disk = execute_runtime_tool(
        "machine.disk_usage",
        {},
        source_context={"surface": "test", "workspace_root": str(ws)},
    )
    assert disk is not None and disk.handled
    assert "cache" not in (disk.details or {})
    assert "cache" not in (disk.details or {}).get("observation", {})

    # structural truth: the registration lines in runtime_execution_tools are
    # the WHOLE opt-in surface — exactly the two named intents, and the live
    # registry holds exactly those
    import core.runtime_execution_tools as ret

    source = Path(ret.__file__).read_text(encoding="utf-8")
    registered = re.findall(r'register_pure_tool\("([^"]+)"', source)
    assert registered == list(_RUNTIME_REGISTERED_TOOLS)
    assert set(tool_memo._PURE_TOOLS) == set(_RUNTIME_REGISTERED_TOOLS)


# ── 7. Provider honesty — the stamp never claims a provider cache ────────────


def test_cache_stamp_carries_exactly_the_measured_keys_and_no_provider_claim(memo_env):
    ws = _workspace(memo_env, "ws-provider")
    miss = _identity(memo_env, ws)
    hit = _identity(memo_env, ws)

    for verdict in (miss.details["cache"], hit.details["cache"]):
        # EXACTLY the four measured keys — no "provider" key, no truthy
        # provider-cache hit hiding beside them
        assert set(verdict) == {"cache_hit", "measured", "memo_tool", "policy_epoch"}
        assert "provider" not in verdict
        assert verdict["measured"] is True
        assert isinstance(verdict["cache_hit"], bool)

    # docstring law over the two module sources: provider_cache exists only as
    # the turn-context receipt's honest unmeasured constant — never a True
    # provider-cache verdict anywhere
    memo_src = Path(tool_memo.__file__).read_text(encoding="utf-8")
    import core.turn_context as turn_context

    turn_src = Path(turn_context.__file__).read_text(encoding="utf-8")
    assert '"provider_cache": "unmeasured"' in turn_src, (
        "the turn-context receipt must carry the honest unmeasured marker"
    )
    # every ASSIGNMENT of provider_cache across both modules is the constant
    assigned = re.findall(r"[\"']provider_cache[\"']\s*[:=]\s*([^,}\n]+)", memo_src + turn_src)
    assert [value.strip() for value in assigned] == ['"unmeasured"']
    # no line in either module pairs provider_cache with a True verdict
    for module_name, source in (("core.tool_memo", memo_src), ("core.turn_context", turn_src)):
        for line in source.splitlines():
            if "provider_cache" in line:
                assert "True" not in line, f"{module_name} claims a provider-cache hit: {line!r}"
    # the memo layer itself never even names a provider cache: the only place
    # the phrase may live is the turn-context receipt's unmeasured marker
    assert "provider_cache" not in memo_src


# ── 8. The second registered tool stamps the same measured truth ─────────────


def test_machine_inspect_specs_stamps_measured_truth_too(memo_env):
    from core.runtime_execution_tools import execute_runtime_tool

    ws = _workspace(memo_env, "ws-specs")
    context = {"surface": "test", "workspace_root": str(ws)}
    first = execute_runtime_tool("machine.inspect_specs", {}, source_context=context)
    assert first.details["cache"]["cache_hit"] is False
    assert first.details["cache"]["measured"] is True
    assert first.details["cache"]["memo_tool"] == "machine.inspect_specs"
    assert first.details["cache"]["policy_epoch"] == policy_epoch()

    second = execute_runtime_tool("machine.inspect_specs", {}, source_context=context)
    assert second.details["cache"]["cache_hit"] is True
    assert second.details["observation"]["cache"]["cache_hit"] is True
    assert second.response_text == first.response_text
    assert memo_stats("machine.inspect_specs") == {"entries": 1, "hits": 1}


# ── 9. invalidate_tool physically drops rows ─────────────────────────────────


def test_invalidate_tool_drops_rows_while_epoch_and_version_merely_orphan(memo_env):
    """``invalidate_tool`` is REMOVAL; epoch/version are UNREACHABILITY.

    The bump and version tests above measured the difference: after a policy or
    version invalidation the old rows stay in ``tool_memo_cache`` (memo_stats
    entries keep counting them) and only the key stops reaching them. Here the
    operator asks for the bytes themselves: rows deleted (returns > 0), the
    accounting drops to zero, and the next call is an honest miss that
    re-records.
    """
    ws = _workspace(memo_env, "ws-invalidate")
    _identity(memo_env, ws)  # miss → one row
    _identity(memo_env, ws)  # hit
    assert memo_stats("workspace.identity")["entries"] == 1

    deleted = tool_memo.invalidate_tool("workspace.identity")
    assert deleted > 0
    assert memo_stats("workspace.identity") == {"entries": 0, "hits": 0}

    after = _identity(memo_env, ws)
    assert after.details["cache"]["cache_hit"] is False, (
        "a physically deleted memo must re-execute, never serve"
    )
    assert memo_stats("workspace.identity") == {"entries": 1, "hits": 0}

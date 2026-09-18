"""Tool memoization — the purity gate and the receipted hit counter.

Sabotage contract (CLAUDE.md §6b.4): if the purity gate ever lets an
unregistered tool be cached, or if an expired memo is served as a hit, the
test named for that cause must fail.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import core.tool_memo as tool_memo
from core.tool_memo import is_pure, memo_key, memo_stats, memoize, record, register_pure_tool


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(tool_memo, "_PURE_TOOLS", {})


def test_unregistered_tools_are_never_memoized_fail_closed_gate():
    calls = []

    def impure():
        calls.append(1)
        return {"ok": True}

    first = memoize("workspace.write_file", {"path": "x"}, impure)
    second = memoize("workspace.write_file", {"path": "x"}, impure)
    assert first[1] is False and second[1] is False
    assert len(calls) == 2, "unregistered tool was served from memo — purity gate open"
    # Scoped since 2026-08-30: lawful registrations exist at runtime_execution_tools
    # import time (workspace.identity, machine.inspect_specs), so the GLOBAL table
    # is no longer provably empty — but an unregistered tool must never gain an entry.
    assert memo_stats("workspace.write_file")["entries"] == 0
    assert not is_pure("workspace.write_file")


def test_registered_pure_tool_executes_once_then_serves_hits():
    calls = []

    def pure():
        calls.append(1)
        return {"rate": 0.91}

    register_pure_tool("currency.table", ttl_seconds=0)
    result, hit = memoize("currency.table", {"date": "2026-08-29"}, pure)
    assert hit is False and result == {"rate": 0.91}
    again, again_hit = memoize("currency.table", {"date": "2026-08-29"}, pure)
    assert again_hit is True
    assert again == {"rate": 0.91}
    assert len(calls) == 1, "pure tool re-executed on an identical key"
    assert memo_stats("currency.table")["hits"] == 1


def test_different_arguments_do_not_collide():
    register_pure_tool("currency.table")
    memoize("currency.table", {"base": "USD"}, lambda: {"rate": 1})
    _, hit = memoize("currency.table", {"base": "RUB"}, lambda: {"rate": 2})
    assert hit is False


def test_expired_ttl_is_a_miss_not_a_hit():
    # THE TTL LAW. Sabotage: serve an expired memo. This test names the cause.
    register_pure_tool("rates.daily", ttl_seconds=60)
    frozen = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    record("rates.daily", memo_key("rates.daily", {"day": "d1"}), {"rate": 90}, now=frozen)
    live, hit = tool_memo.lookup(
        "rates.daily", memo_key("rates.daily", {"day": "d1"}), now=frozen + timedelta(seconds=30)
    )
    assert hit is True and live == {"rate": 90}
    # an expired lookup is a miss: lookup returns None, and memoize re-executes
    assert (
        tool_memo.lookup(
            "rates.daily", memo_key("rates.daily", {"day": "d1"}), now=frozen + timedelta(seconds=61)
        )
        is None
    ), "expired memo served as a hit"


def test_unserializable_results_are_executed_but_not_cached():
    register_pure_tool("weird.pure")
    obj = object()
    result, hit = memoize("weird.pure", {}, lambda: obj)
    assert result is obj and hit is False
    assert memo_stats("weird.pure")["entries"] == 0


def test_registration_gate_rejects_bad_declarations():
    with pytest.raises(ValueError):
        register_pure_tool("")
    with pytest.raises(ValueError):
        register_pure_tool("x", ttl_seconds=-1)


def test_memo_key_is_canonical_over_argument_order():
    a = memo_key("t", {"a": 1, "b": 2})
    b = memo_key("t", {"b": 2, "a": 1})
    assert a == b, "key must not depend on dict ordering"
    assert a != memo_key("t", {"a": 1, "b": 3})
    assert a != memo_key("t", {"a": 1, "b": 2}, scope=["ws:other"])


def test_scope_changes_the_key_so_foreign_state_cannot_serve():
    register_pure_tool("ws.list")
    memoize("ws.list", {}, lambda: ["old"], scope=["ws:hash-1"])
    _, hit = memoize("ws.list", {}, lambda: ["new"], scope=["ws:hash-2"])
    assert hit is False, "a memo from another workspace state was served"


def test_json_like_results_round_trip_through_the_memo():
    register_pure_tool("structured.pure")
    payload = {"nums": [1, 2, 3], "nested": {"ok": True}}
    memoize("structured.pure", {}, lambda: payload)
    served, hit = memoize("structured.pure", {}, lambda: payload)
    assert hit is True
    assert served == json.loads(json.dumps(payload))

"""Regression tests for the 2026-07-22 multi-agent-sweep fixes.

Each locks a live-usage failure found by the sweep and fixed in commit 81d4b2c:
 - reliability: a chat-surface turn must not be validated against a JSON action_plan/summary_block
   contract (that marked plain prose contract_failed -> "couldn't get a usable model response").
 - web search: WEB_SEARCH_PROVIDER_ORDER is ORDER, not permission -- a stale env must never drop the
   keyless google_html fallback.
 - context bleed: audit/eval turns (memory_scope=ephemeral) must execute but never persist to the
   shared live memory store, or cross-session recall replays them into a real user's later prompt.
"""

from __future__ import annotations

from core.task_router import model_execution_profile


def test_chat_surface_turns_resolve_to_plain_text_not_a_json_contract() -> None:
    # Classes that map to a JSON contract off-surface must become plain_text on a chat surface, so a
    # normal conversational/creative answer is not rejected as contract_failed.
    for cls in ("system_design", "debugging", "config", "dependency_resolution", "unknown", "research"):
        assert model_execution_profile(cls, chat_surface=False)["output_mode"] != "plain_text"
        assert model_execution_profile(cls, chat_surface=True)["output_mode"] == "plain_text"
        # An explicit planner-style request still keeps the structured contract on a chat surface.
        assert model_execution_profile(cls, chat_surface=True, planner_style_requested=True)["output_mode"] == "action_plan"


def test_web_provider_order_keeps_google_html_reachable_under_a_stale_env(monkeypatch) -> None:
    # The launcher-baked default predates google_html; treating the env as an allowlist dropped the
    # only reliable keyless general-web provider, leaving searxng(down)+duckduckgo_html(blocked)+
    # ddg_instant(encyclopedia) = zero hits.
    import tools.web.web_research as wr

    monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "searxng,ddg_instant,duckduckgo_html")
    order = wr._provider_order()
    assert "google_html" in order, order
    # env order is still honored first (searxng stays ahead of the appended engines)
    assert order.index("searxng") < order.index("google_html")


def test_memory_persistence_guard_suppresses_audit_turns_only(monkeypatch) -> None:
    from core.persistent_memory import _memory_persistence_suppressed

    monkeypatch.delenv("VOOL_EPHEMERAL_MEMORY", raising=False)
    # Audit/eval turns: execute but never persist.
    assert _memory_persistence_suppressed({"memory_scope": "ephemeral"}) is True
    assert _memory_persistence_suppressed({"memory_scope": "audit"}) is True
    assert _memory_persistence_suppressed({"persist_memory": False}) is True
    assert _memory_persistence_suppressed({"ephemeral_memory": True}) is True
    # Live turns persist as before (no false positives from a normal surface).
    assert _memory_persistence_suppressed({}) is False
    assert _memory_persistence_suppressed(None) is False
    assert _memory_persistence_suppressed({"surface": "api", "memory_scope": "live"}) is False
    # Env kill-switch.
    monkeypatch.setenv("VOOL_EPHEMERAL_MEMORY", "1")
    assert _memory_persistence_suppressed({}) is True

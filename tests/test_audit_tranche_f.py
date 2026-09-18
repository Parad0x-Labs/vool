"""Audit lows: no-web directive honored, DB-switch invalidation (schema + pooled conn), and
case-sensitive path denial on Linux."""
from __future__ import annotations

import sys

from core.agent_runtime.fast_live_info_price import looks_like_grounded_price_lookup, user_forbids_web


# --- #14: an explicit no-web directive suppresses the live fetch fast path ---
def test_no_web_directive_is_detected():
    for msg in (
        "Do NOT use the web. From your own knowledge only: bitcoin price yesterday?",
        "without using the internet, what is the price of eth",
        "from your own knowledge, latest news on X",
        "offline only: newest gpt price",
    ):
        assert user_forbids_web(msg), msg


def test_no_web_directive_disables_the_price_fast_path():
    # A price question that WOULD trigger the live path is suppressed when web is forbidden.
    yes = "what is the current price of bitcoin"
    assert looks_like_grounded_price_lookup(yes) is True
    no = "do not use the web: what is the current price of bitcoin"
    assert looks_like_grounded_price_lookup(no) is False


def test_ordinary_questions_are_not_flagged_as_no_web():
    for msg in ("what is the price of bitcoin", "give me the latest news", "search the web for X"):
        assert user_forbids_web(msg) is False, msg


# --- #22: a connection always reflects the ACTIVE default DB path at open time ---
def test_connection_switches_on_db_path_change(tmp_path):
    from storage.db import configure_default_db_path, get_connection

    a = str(tmp_path / "a.db")
    b = str(tmp_path / "b.db")
    try:
        configure_default_db_path(a)
        c1 = get_connection()
        p1 = c1.execute("PRAGMA database_list").fetchall()[0][2]
        c1.close()
        configure_default_db_path(b)
        c2 = get_connection()
        p2 = c2.execute("PRAGMA database_list").fetchall()[0][2]
        c2.close()
        assert p1.endswith("a.db") and p2.endswith("b.db"), "connections must follow the db switch"
    finally:
        configure_default_db_path(None)


# --- #21: usage schema is (re)created on the ACTIVE db, not skipped after a switch ---
def test_usage_schema_recreated_on_new_db(tmp_path):
    import core.usage_meter as um
    from core.usage_meter import record_usage, usage_summary
    from storage.db import configure_default_db_path

    a = str(tmp_path / "a.db")
    b = str(tmp_path / "b.db")
    try:
        configure_default_db_path(a)
        um._SCHEMA_READY_PATHS.clear()
        assert record_usage(provider_id="p", model_id="m", cost_class="free_local", prompt_tokens=5, output_tokens=5)
        assert usage_summary()["total_tokens"] == 10
        # New db: schema must be created here (a single global flag would have skipped it).
        configure_default_db_path(b)
        assert record_usage(provider_id="p", model_id="m", cost_class="free_local", prompt_tokens=3, output_tokens=3)
        assert usage_summary()["total_tokens"] == 6, "metering must land on the new db, not be skipped"
        assert a in um._SCHEMA_READY_PATHS and b in um._SCHEMA_READY_PATHS
    finally:
        configure_default_db_path(None)


# --- #24: path denial is case-sensitive on Linux, case-insensitive on macOS ---
def test_path_denied_respects_platform_case_sensitivity():
    from pathlib import Path

    from core.operator.storage import path_is_denied

    def policy_get(_key, _default):
        return ["/data/Secret"]

    exact = path_is_denied(Path("/data/Secret/x"), policy_get=policy_get)
    diff_case = path_is_denied(Path("/data/secret/x"), policy_get=policy_get)
    assert exact is True
    if sys.platform.startswith("linux"):
        assert diff_case is False, "distinct-case paths are different files on Linux"
    else:
        assert diff_case is True  # macOS/Windows case-insensitive FS

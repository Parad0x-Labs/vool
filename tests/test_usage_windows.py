"""Per-model token totals and calendar windows (today / week / month / custom).

Calendar boundaries are computed in LOCAL time (Windows-safe, no tz-name strings), so the
expectations here are derived with the same local-time logic rather than hard-coded — the test
is correct in any timezone. The ledger itself stays UTC epoch.
"""
from __future__ import annotations

from datetime import datetime, timezone

import core.usage_meter as um
from core.usage_meter import (
    COST_FREE_LOCAL,
    COST_PAID_CLOUD,
    record_usage,
    resolve_window,
    usage_by_model,
    usage_summary,
)

# A Wednesday: 2023-11-15 12:00:00 UTC. Fixed so windowing is deterministic.
NOW = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _reset() -> None:
    um._SCHEMA_READY_PATHS.clear()
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    finally:
        conn.close()


def _local_midnight(now_ts: float) -> datetime:
    return datetime.fromtimestamp(now_ts).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


# --- resolve_window ---------------------------------------------------------------


def test_today_starts_at_local_midnight_and_ends_now():
    w = resolve_window(range_="today", now=NOW)
    assert w["range"] == "today"
    assert w["until_ts"] == NOW
    assert abs(w["since_ts"] - _local_midnight(NOW).timestamp()) < 1.0


def test_week_starts_on_local_monday():
    w = resolve_window(range_="week", now=NOW)
    start = datetime.fromtimestamp(w["since_ts"]).astimezone()
    assert start.weekday() == 0 and start.hour == 0  # Monday, midnight
    assert w["since_ts"] <= NOW


def test_month_starts_on_the_first():
    w = resolve_window(range_="month", now=NOW)
    start = datetime.fromtimestamp(w["since_ts"]).astimezone()
    assert start.day == 1 and start.hour == 0


def test_custom_date_range_is_inclusive_of_the_until_day():
    w = resolve_window(since="2023-11-01", until="2023-11-15", now=NOW)
    assert w["range"] == "custom"
    since = datetime.fromtimestamp(w["since_ts"]).astimezone()
    until = datetime.fromtimestamp(w["until_ts"]).astimezone()
    assert since.day == 1 and since.hour == 0
    assert until.day == 16 and until.hour == 0  # advanced one day so the 15th is included


def test_custom_epoch_bounds_pass_through():
    w = resolve_window(since=str(NOW - 100), until=str(NOW), now=NOW)
    assert abs(w["since_ts"] - (NOW - 100)) < 1e-6 and abs(w["until_ts"] - NOW) < 1e-6


def test_named_range_wins_over_since_until():
    w = resolve_window(range_="today", since="2000-01-01", now=NOW)
    assert w["range"] == "today"  # the named range takes precedence


def test_empty_window_is_unbounded():
    w = resolve_window(now=NOW)
    assert w["since_ts"] is None and w["until_ts"] is None and w["range"] == ""


def test_out_of_range_epoch_is_rejected_not_crashed():
    # A JavaScript millisecond timestamp mistaken for seconds must not raise (would 500 the endpoint).
    w = resolve_window(since="1752883200000", until="99999999999999999999", now=NOW)
    assert w["since_ts"] is None and w["until_ts"] is None
    assert w["since"] is None and w["until"] is None
    # usage_summary must also survive the bad bounds without raising.
    s = usage_summary(since_ts=None, until_ts=None)
    assert "total_tokens" in s


def test_dst_boundaries_land_on_local_midnight_both_sides():
    # Regardless of a DST transition between now and the boundary, each boundary is local midnight.
    for key in ("today", "week", "month"):
        w = resolve_window(range_=key, now=NOW)
        start = datetime.fromtimestamp(w["since_ts"]).astimezone()
        assert (start.hour, start.minute, start.second) == (0, 0, 0), key


# --- usage_by_model ---------------------------------------------------------------


def test_per_model_groups_and_sorts_by_tokens():
    _reset()
    record_usage(provider_id="ollama", model_id="qwen-local", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=10, output_tokens=5, now=NOW)
    record_usage(provider_id="openrouter", model_id="tencent/hy3:free", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=100, output_tokens=200, now=NOW)
    record_usage(provider_id="openrouter", model_id="openai/gpt-4.1", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=40, output_tokens=60, usd_actual=0.02, now=NOW)
    rows = usage_by_model()
    assert [r["model_id"] for r in rows] == ["tencent/hy3:free", "openai/gpt-4.1", "qwen-local"]
    paid = next(r for r in rows if r["model_id"] == "openai/gpt-4.1")
    assert paid["cost_class"] == COST_PAID_CLOUD and paid["usd"] == 0.02 and paid["all_actual"] is True
    assert paid["total_tokens"] == 100


def test_per_model_respects_absolute_bounds():
    _reset()
    record_usage(provider_id="p", model_id="old", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=7, output_tokens=0, now=NOW - 10 * 86400)
    record_usage(provider_id="p", model_id="new", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=9, output_tokens=0, now=NOW - 3600)
    rows = usage_by_model(since_ts=NOW - 86400, until_ts=NOW)
    assert [r["model_id"] for r in rows] == ["new"], "the 10-day-old row is outside the window"


def test_unpriced_paid_row_uses_the_blended_estimate():
    _reset()
    record_usage(provider_id="openrouter", model_id="x/y", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=500000, output_tokens=500000, now=NOW)  # no usd_actual
    rows = usage_by_model()
    row = rows[0]
    assert row["all_actual"] is False and row["usd"] > 0  # ~1M tokens * blended rate


# --- usage_summary absolute bounds ------------------------------------------------


def test_summary_absolute_window_excludes_the_future_and_past():
    _reset()
    record_usage(provider_id="p", model_id="m", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=5, output_tokens=5, now=NOW - 10)
    record_usage(provider_id="p", model_id="m", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=5, output_tokens=5, now=NOW + 10 * 86400)  # far future
    s = usage_summary(since_ts=NOW - 86400, until_ts=NOW)
    assert s[COST_FREE_LOCAL]["total_tokens"] == 10  # only the in-window row
    assert s["since"] is not None and s["until"] is not None

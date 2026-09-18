"""GET /api/runtime/usage honors range / since-until / per_model while staying backward
compatible with the legacy ?window= trailing window."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import core.usage_meter as um
from core.usage_meter import COST_FREE_LOCAL, COST_PAID_CLOUD, record_usage
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get

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


def _usage(query):
    res = dispatch_get(path="/api/runtime/usage", query=query, runtime=RuntimeServices(display_name="VOOL"), model_name="vool")
    return json.loads(res.body.decode("utf-8"))


def test_per_model_breakdown_is_opt_in():
    _reset()
    record_usage(provider_id="openrouter", model_id="openai/gpt-4.1", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=10, output_tokens=10, usd_actual=0.01, now=NOW)
    assert "by_model" not in _usage({})
    payload = _usage({"per_model": ["1"]})
    assert isinstance(payload["by_model"], list)
    assert payload["by_model"][0]["model_id"] == "openai/gpt-4.1"


def test_range_echoes_resolved_window():
    _reset()
    record_usage(provider_id="ollama", model_id="local", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=3, output_tokens=3, now=NOW)
    payload = _usage({"range": ["month"]})
    assert payload["window"]["range"] == "month"
    assert payload["window"]["since"] is not None


def test_since_until_custom_window():
    payload = _usage({"since": ["2023-11-01"], "until": ["2023-11-30"]})
    assert payload["window"]["range"] == "custom"


def test_legacy_window_param_still_works():
    payload = _usage({"window": ["3600"]})
    assert "window" not in payload  # trailing-window mode carries `since`, not the range meta
    assert "total_tokens" in payload


def test_out_of_range_epoch_does_not_500():
    # A JS millisecond timestamp in ?since must not raise an unhandled 500.
    payload = _usage({"since": ["1752883200000"]})
    assert "total_tokens" in payload
    assert payload["window"]["since"] is None  # bad bound ignored, request still served

"""`cloud usage [today|week|month|all]` — read-only token report from the real ledger."""
from __future__ import annotations

import pytest

import core.usage_meter as um
from core.agent_runtime.fast_command_surface import maybe_handle_cloud_usage_command
from core.usage_meter import COST_FREE_LOCAL, COST_PAID_CLOUD, record_usage


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    um._SCHEMA_READY_PATHS.clear()
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    finally:
        conn.close()
    yield
    runtime_paths.configure_runtime_home(None)


def test_non_command_falls_through():
    """A non-command must fall through for ANY caller, so the owner gate cannot hijack chat."""
    assert maybe_handle_cloud_usage_command("what did I use today?", owner_local=True) is None
    assert maybe_handle_cloud_usage_command("cloud models", owner_local=True) is None
    assert maybe_handle_cloud_usage_command("what did I use today?", owner_local=False) is None


def test_remote_surface_gets_no_usage_history():
    """The ledger names the models this machine runs and what they cost — owner-local only.
    Read-only is not the same as public."""
    record_usage(provider_id="openrouter", model_id="openai/gpt-4.1", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=20, output_tokens=30, usd_actual=0.05)
    out = maybe_handle_cloud_usage_command("cloud usage all", owner_local=False)
    assert out is not None and "owner-local" in out
    assert "openai/gpt-4.1" not in out and "0.05" not in out
    assert "By model:" not in out


def test_empty_ledger_reports_no_usage():
    out = maybe_handle_cloud_usage_command("cloud usage", owner_local=True)
    assert out is not None and "all time" in out
    assert "No usage recorded" in out


def test_report_shows_per_model_rows_and_paid_dollars():
    record_usage(provider_id="ollama", model_id="qwen-local", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=50, output_tokens=50)
    record_usage(provider_id="openrouter", model_id="openai/gpt-4.1", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=20, output_tokens=30, usd_actual=0.05)
    out = maybe_handle_cloud_usage_command("cloud usage all", owner_local=True)
    assert "By model:" in out
    assert "openai/gpt-4.1" in out and "qwen-local" in out
    assert "$0.0500" in out  # exact provider-reported charge, no ~ prefix


def test_window_header_reflects_the_span():
    assert "this week" in maybe_handle_cloud_usage_command("cloud usage week", owner_local=True)
    assert "this month" in maybe_handle_cloud_usage_command("cloud usage month", owner_local=True)
    assert "today" in maybe_handle_cloud_usage_command("cloud usage today", owner_local=True)

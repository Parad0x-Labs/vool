from __future__ import annotations

from core.llm_eval.metrics import summarize_latency_rows
from ops import run_local_acceptance as acceptance


def test_locked_speed_profile_remains_concrete() -> None:
    profile = acceptance.load_profile()

    assert profile.cold_start_max_seconds > 0
    assert profile.simple_prompt_median_max_seconds > 0
    assert profile.file_task_median_max_seconds > 0
    assert profile.live_lookup_median_max_seconds > 0
    assert profile.chained_task_median_max_seconds > 0


def test_latency_summary_reports_percentiles_for_real_acceptance_rows() -> None:
    rows = [
        {"request_type": "warm_simple", "latency_seconds": 1.2},
        {"request_type": "warm_simple", "latency_seconds": 1.5},
        {"request_type": "warm_simple", "latency_seconds": 1.8},
        {"request_type": "research_lookup", "latency_seconds": 4.8},
        {"request_type": "research_lookup", "latency_seconds": 6.1},
    ]

    summary = summarize_latency_rows(rows)

    assert summary["samples"] == 5
    assert summary["overall"]["p50"] is not None
    assert summary["by_request_type"]["warm_simple"]["p95"] is not None
    assert summary["by_request_type"]["research_lookup"]["max"] == 6.1

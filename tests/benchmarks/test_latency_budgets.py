from __future__ import annotations

from core.llm_eval.metrics import percentile, summarize_latency_rows


def test_percentile_interpolates_latency_samples() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) is not None


def test_latency_summary_groups_by_request_type() -> None:
    summary = summarize_latency_rows(
        [
            {"request_type": "warm_simple", "latency_seconds": 1.1},
            {"request_type": "warm_simple", "latency_seconds": 1.5},
            {"request_type": "tool_invocation", "latency_seconds": 3.2},
        ]
    )

    assert summary["samples"] == 3
    assert summary["by_request_type"]["warm_simple"]["samples"] == 2
    assert summary["by_request_type"]["tool_invocation"]["max"] == 3.2

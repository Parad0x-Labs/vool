from __future__ import annotations

import asyncio
import csv
import sys
import types
from pathlib import Path

from ops import greenloop_concurrency
from ops.greenloop_concurrency import (
    RequestMeasurement,
    _ensure_runtime_warm,
    _parse_levels,
    _write_summary_csv,
    summarize_measurements,
)


def test_parse_levels_rejects_zero() -> None:
    try:
        _parse_levels("1,0,4")
    except ValueError as exc:
        assert "positive" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ValueError")


def test_summarize_measurements_counts_successes_and_percentiles() -> None:
    rows = [
        RequestMeasurement(concurrency=2, request_index=0, success=True, latency_seconds=0.2, status_code=200, response_bytes=10, token="a"),
        RequestMeasurement(concurrency=2, request_index=1, success=False, latency_seconds=0.8, status_code=500, response_bytes=0, token="b", error="boom"),
        RequestMeasurement(concurrency=2, request_index=2, success=True, latency_seconds=0.4, status_code=200, response_bytes=10, token="c"),
        RequestMeasurement(concurrency=2, request_index=3, success=True, latency_seconds=0.6, status_code=200, response_bytes=10, token="d"),
    ]
    summary = summarize_measurements(rows, wall_seconds=2.0)
    assert summary["total_requests"] == 4
    assert summary["successes"] == 3
    assert summary["success_rate"] == 0.75
    assert summary["throughput_rps"] == 1.5
    assert summary["p50_seconds"] == 0.5
    assert summary["max_seconds"] == 0.8


def test_write_summary_csv_writes_expected_columns(tmp_path) -> None:
    output = tmp_path / "concurrency.csv"
    _write_summary_csv(
        output,
        [
            {
                "concurrency": 1,
                "total_requests": 4,
                "successes": 4,
                "success_rate": 1.0,
                "throughput_rps": 1.234,
                "wall_seconds": 3.24,
                "p50_seconds": 0.4,
                "p95_seconds": 0.6,
                "p99_seconds": 0.7,
                "max_seconds": 0.8,
                "mean_seconds": 0.5,
            }
        ],
    )
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "concurrency": "1",
            "total_requests": "4",
            "successes": "4",
            "success_rate": "1.0",
            "throughput_rps": "1.234",
            "wall_seconds": "3.24",
            "p50_seconds": "0.4",
            "p95_seconds": "0.6",
            "p99_seconds": "0.7",
            "max_seconds": "0.8",
            "mean_seconds": "0.5",
        }
    ]


def test_ensure_runtime_warm_retries_until_success(monkeypatch, tmp_path: Path) -> None:
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    attempts: list[int] = []

    async def _fake_run_probe(
        client,
        *,
        base_url: str,
        workspace_root: Path,
        token: str,
        conversation_id: str,
        workspace_suffix: str,
        concurrency: int,
        request_index: int,
    ) -> RequestMeasurement:
        attempts.append(request_index)
        return RequestMeasurement(
            concurrency=concurrency,
            request_index=request_index,
            success=request_index >= 2,
            latency_seconds=0.5,
            status_code=200 if request_index >= 2 else 503,
            response_bytes=10,
            token=token,
            error=None if request_index >= 2 else "warming",
        )

    monkeypatch.setattr(greenloop_concurrency, "_run_probe", _fake_run_probe)
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=lambda timeout: _FakeClient()))

    result = asyncio.run(
        _ensure_runtime_warm(
            base_url="http://127.0.0.1:18080",
            workspace_root=tmp_path,
            timeout_seconds=30.0,
            warmup_attempts=3,
        )
    )

    assert attempts == [1, 2]
    assert result.success is True
    assert result.request_index == 2


def test_ensure_runtime_warm_raises_after_exhausting_attempts(monkeypatch, tmp_path: Path) -> None:
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def _fake_run_probe(
        client,
        *,
        base_url: str,
        workspace_root: Path,
        token: str,
        conversation_id: str,
        workspace_suffix: str,
        concurrency: int,
        request_index: int,
    ) -> RequestMeasurement:
        return RequestMeasurement(
            concurrency=concurrency,
            request_index=request_index,
            success=False,
            latency_seconds=0.5,
            status_code=504,
            response_bytes=0,
            token=token,
            error="timed out",
        )

    monkeypatch.setattr(greenloop_concurrency, "_run_probe", _fake_run_probe)
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=lambda timeout: _FakeClient()))

    try:
        asyncio.run(
            _ensure_runtime_warm(
                base_url="http://127.0.0.1:18080",
                workspace_root=tmp_path,
                timeout_seconds=30.0,
                warmup_attempts=2,
            )
        )
    except RuntimeError as exc:
        assert "runtime warmup failed after 2 attempt" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected RuntimeError")

"""Prometheus-compatible metrics endpoint and in-process metrics collector."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class MetricsCollector:
    """Thread-safe in-process metrics for VOOL nodes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()

    def inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            bucket = self._histograms[key]
            bucket.append(value)
            if len(bucket) > 10000:
                self._histograms[key] = bucket[-5000:]

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def render_prometheus(self) -> str:
        lines: list[str] = []
        lines.append(f"# VOOL Metrics - uptime {time.time() - self._start_time:.0f}s")
        with self._lock:
            for key, val in sorted(self._counters.items()):
                lines.append(f"vool_{key} {val}")
            for key, val in sorted(self._gauges.items()):
                lines.append(f"vool_{key} {val}")
            for key, vals in sorted(self._histograms.items()):
                if vals:
                    lines.append(f"vool_{key}_count {len(vals)}")
                    lines.append(f"vool_{key}_sum {sum(vals):.4f}")
                    sorted_vals = sorted(vals)
                    lines.append(f"vool_{key}_p50 {sorted_vals[len(sorted_vals)//2]:.4f}")
                    lines.append(f"vool_{key}_p99 {sorted_vals[int(len(sorted_vals)*0.99)]:.4f}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histogram_counts": {k: len(v) for k, v in self._histograms.items()},
                "uptime_seconds": round(time.time() - self._start_time, 1),
            }


# Global singleton
_METRICS = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _METRICS

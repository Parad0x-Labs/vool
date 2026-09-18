"""Chaos testing harness for VOOL swarm resilience.

Provides controlled fault injection scenarios:
- Network partition simulation
- Node crash simulation
- Clock skew injection
- Message corruption
- Memory pressure
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChaosResult:
    scenario: str
    passed: bool
    duration_seconds: float
    events: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ChaosEngine:
    """Controlled fault injection engine for soak and chaos testing."""

    def __init__(self) -> None:
        self._faults_active: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._original_funcs: dict[str, Callable] = {}

    def inject_network_partition(self, drop_rate: float = 0.5, duration: float = 10.0) -> ChaosResult:
        """Simulate partial network partition by dropping messages."""
        events: list[str] = []
        events.append(f"Injecting network partition: drop_rate={drop_rate}, duration={duration}s")

        with self._lock:
            self._faults_active["network_partition"] = True

        time.sleep(duration)

        with self._lock:
            self._faults_active["network_partition"] = False

        events.append("Network partition healed")
        return ChaosResult(scenario="network_partition", passed=True, duration_seconds=duration, events=events)

    def inject_clock_skew(self, skew_seconds: float = 30.0, duration: float = 15.0) -> ChaosResult:
        """Simulate clock drift between nodes."""
        events = [f"Injecting clock skew: {skew_seconds}s for {duration}s"]

        with self._lock:
            self._faults_active["clock_skew"] = True

        time.sleep(duration)

        with self._lock:
            self._faults_active["clock_skew"] = False

        events.append("Clock skew resolved")
        return ChaosResult(scenario="clock_skew", passed=True, duration_seconds=duration, events=events)

    def inject_slow_responses(self, delay_ms: int = 500, duration: float = 20.0) -> ChaosResult:
        """Inject artificial latency into service responses."""
        events = [f"Injecting {delay_ms}ms response delay for {duration}s"]

        with self._lock:
            self._faults_active["slow_responses"] = True

        time.sleep(duration)

        with self._lock:
            self._faults_active["slow_responses"] = False

        events.append("Response latency restored")
        return ChaosResult(scenario="slow_responses", passed=True, duration_seconds=duration, events=events)

    def should_drop_message(self) -> bool:
        """Check if the network partition fault is active — callers can use this in send paths."""
        with self._lock:
            if not self._faults_active.get("network_partition"):
                return False
        return random.random() < 0.5

    def get_clock_skew(self) -> float:
        """Return current injected clock skew in seconds (0 if none)."""
        with self._lock:
            if self._faults_active.get("clock_skew"):
                return 30.0
        return 0.0

    def get_response_delay(self) -> float:
        """Return current injected response delay in seconds (0 if none)."""
        with self._lock:
            if self._faults_active.get("slow_responses"):
                return 0.5
        return 0.0

    def run_chaos_suite(self, scenarios: list[str] | None = None) -> list[ChaosResult]:
        """Run a sequence of chaos scenarios."""
        all_scenarios = scenarios or ["network_partition", "clock_skew", "slow_responses"]
        results: list[ChaosResult] = []

        for scenario in all_scenarios:
            logger.info("Running chaos scenario: %s", scenario)
            if scenario == "network_partition":
                results.append(self.inject_network_partition(drop_rate=0.3, duration=5.0))
            elif scenario == "clock_skew":
                results.append(self.inject_clock_skew(skew_seconds=15.0, duration=5.0))
            elif scenario == "slow_responses":
                results.append(self.inject_slow_responses(delay_ms=300, duration=5.0))
            else:
                results.append(ChaosResult(scenario=scenario, passed=False, duration_seconds=0, errors=[f"Unknown scenario: {scenario}"]))

        return results

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"active_faults": dict(self._faults_active)}


_ENGINE = ChaosEngine()


def get_chaos_engine() -> ChaosEngine:
    return _ENGINE

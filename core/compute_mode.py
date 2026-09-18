"""Adaptive compute mode: max-push when idle, balanced when user is present.

Two modes:
  max_push  – user away / machine idle → full GPU + CPU allocation
  balanced  – user at keyboard → throttle to ~50-60% so the machine stays snappy

The daemon polls system idle time and switches automatically.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from threading import Event, RLock, Thread


@dataclass
class ComputeBudget:
    mode: str  # "max_push" | "balanced"
    cpu_threads: int
    gpu_memory_fraction: float  # 0.0–1.0
    worker_pool_cap: int
    reason: str


_POLL_INTERVAL_SECONDS = 15
_IDLE_THRESHOLD_SECONDS = 120  # 2 min without input → idle
_ACTIVE_BUDGET = ComputeBudget(
    mode="balanced",
    cpu_threads=max(1, (os.cpu_count() or 2) // 2),
    gpu_memory_fraction=0.0,
    worker_pool_cap=max(1, (os.cpu_count() or 2) // 4),
    reason="Default balanced budget before daemon startup",
)
_ACTIVE_BUDGET_LOCK = RLock()


def current_idle_seconds() -> float | None:
    """Return seconds since last user input, or None if detection unavailable."""
    system = platform.system().lower()

    if system == "windows":
        return _win_idle()
    if system == "darwin":
        return _mac_idle()
    if system == "linux":
        return _linux_idle()
    return None


def compute_budget(
    *,
    idle_seconds: float | None = None,
    cpu_cores: int | None = None,
    has_gpu: bool = False,
) -> ComputeBudget:
    cores = cpu_cores or os.cpu_count() or 2
    if idle_seconds is None:
        idle_seconds = current_idle_seconds()
    is_idle = idle_seconds is not None and idle_seconds >= _IDLE_THRESHOLD_SECONDS

    if is_idle:
        return ComputeBudget(
            mode="max_push",
            cpu_threads=max(1, cores - 1),
            gpu_memory_fraction=0.90 if has_gpu else 0.0,
            worker_pool_cap=max(1, cores // 2),
            reason=f"User idle {idle_seconds:.0f}s (>= {_IDLE_THRESHOLD_SECONDS}s threshold)",
        )
    else:
        return ComputeBudget(
            mode="balanced",
            cpu_threads=max(1, cores // 2),
            gpu_memory_fraction=0.50 if has_gpu else 0.0,
            worker_pool_cap=max(1, cores // 4),
            reason="User present — balanced mode",
        )


def set_active_compute_budget(budget: ComputeBudget) -> ComputeBudget:
    with _ACTIVE_BUDGET_LOCK:
        global _ACTIVE_BUDGET
        _ACTIVE_BUDGET = budget
        return _ACTIVE_BUDGET


def get_active_compute_budget() -> ComputeBudget:
    with _ACTIVE_BUDGET_LOCK:
        return ComputeBudget(
            mode=_ACTIVE_BUDGET.mode,
            cpu_threads=int(_ACTIVE_BUDGET.cpu_threads),
            gpu_memory_fraction=float(_ACTIVE_BUDGET.gpu_memory_fraction),
            worker_pool_cap=int(_ACTIVE_BUDGET.worker_pool_cap),
            reason=str(_ACTIVE_BUDGET.reason),
        )


class ComputeModeDaemon:
    """Background thread that monitors idle state and exposes current budget."""

    def __init__(self, *, has_gpu: bool = False):
        self._has_gpu = has_gpu
        self._budget = compute_budget(idle_seconds=0, has_gpu=has_gpu)
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def budget(self) -> ComputeBudget:
        return self._budget

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        set_active_compute_budget(self._budget)
        self._thread = Thread(target=self._poll_loop, daemon=True, name="compute-mode")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            idle = current_idle_seconds()
            self._budget = compute_budget(
                idle_seconds=idle,
                has_gpu=self._has_gpu,
            )
            set_active_compute_budget(self._budget)
            self._stop.wait(_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Platform-specific idle detection
# ---------------------------------------------------------------------------

def _win_idle() -> float | None:
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return float(millis) / 1000.0
    except Exception:
        pass
    return None


def _mac_idle() -> float | None:
    try:
        import subprocess
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=3,
        )
        for line in (result.stdout or "").splitlines():
            if "HIDIdleTime" in line:
                parts = line.split("=")
                if len(parts) >= 2:
                    raw = parts[-1].strip().strip('"')
                    return float(raw) / 1_000_000_000.0  # nanoseconds → seconds
    except Exception:
        pass
    return None


def _linux_idle() -> float | None:
    try:
        import subprocess
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return float(result.stdout.strip()) / 1000.0
    except Exception:
        pass
    return None

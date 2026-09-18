"""Live system-resource sensing — the foundation of VOOL's resource governor.

VOOL used to know the machine only at boot (hardware_tier picks a model size once). This reads it
LIVE: free RAM, memory pressure, CPU load, swap — plus what VOOL's own subsystems are holding
(Ollama's loaded model, ComfyUI). Heavy tasks (an SDXL render, loading a big model) can then be
gated on REAL headroom instead of firing blind and swapping the machine into a beachball freeze.

Design:
- **OS tools only, no psutil** — this runs on the key-holding machine where new package installs are
  banned. macOS uses `sysctl`/`vm_stat`; Linux reads `/proc`; `os.getloadavg` is portable.
- **Every probe is best-effort** — a missing tool degrades one field to a safe default, never raises.
- **Cached ~3s** — a snapshot costs a couple of cheap subprocesses; callers can read it per-turn free.
- **Reserve for the user** — `reserve_gb` (default 20% of total) is RAM the governor keeps free so the
  browser and other apps stay responsive; `usable_gb` is what a heavy task may take without dipping in.
"""
from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass

_GIB = 1024.0 ** 3
_CACHE_TTL = 3.0
_RESERVE_FRACTION = float(os.environ.get("VOOL_RAM_RESERVE_FRACTION", "0.20") or 0.20)


def _reserve_fraction() -> float:
    """RAM fraction to keep free for the user. Explicit env override wins; else the user's Settings
    preference (ram_reserve_pct); else the 20% default. Lazy pref import avoids an import cycle."""
    if os.environ.get("VOOL_RAM_RESERVE_FRACTION"):
        return _RESERVE_FRACTION
    try:
        from core.user_preferences import load_preferences

        pct = int(load_preferences().ram_reserve_pct)
        return min(0.80, max(0.10, pct / 100.0))
    except Exception:
        return _RESERVE_FRACTION
from core.ollama_endpoint import ollama_api_url as _ollama_api_url

_OLLAMA_PS_URL = os.environ.get("VOOL_OLLAMA_PS_URL") or _ollama_api_url("/api/ps")
_COMFYUI_PORT = os.environ.get("VOOL_COMFYUI_PORT", "8188")

_lock = threading.Lock()
_cache: tuple[float, ResourceSnapshot] | None = None


@dataclass(frozen=True)
class ResourceSnapshot:
    total_ram_gb: float
    available_ram_gb: float
    used_ram_gb: float
    free_fraction: float            # available / total, 0..1
    memory_pressure: str            # normal | warn | critical | unknown
    load_1m: float
    cpu_count: int
    load_per_core: float
    swap_used_gb: float
    ollama_loaded_gb: float         # what Ollama is holding in memory right now (0 if none / unreachable)
    comfyui_running: bool
    reserve_gb: float               # RAM to keep free for the user
    usable_gb: float                # max a heavy task may take now: max(0, available - reserve)

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return out.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ------------------------------------------------------------------ RAM (total / available / pressure)

def _macos_total_ram_bytes() -> float:
    raw = _run(["sysctl", "-n", "hw.memsize"]).strip()
    with contextlib.suppress(ValueError):
        return float(int(raw))
    return 0.0


def _macos_ram_available_and_pressure(total_bytes: float) -> tuple[float, str]:
    """Available bytes + a pressure verdict, from vm_stat page counts (no sudo, always works)."""
    text = _run(["vm_stat"])
    if not text:
        return 0.0, "unknown"
    page = 4096
    m = re.search(r"page size of (\d+) bytes", text)
    if m:
        page = int(m.group(1))

    def pages(label: str) -> int:
        mm = re.search(rf"{re.escape(label)}:\s+(\d+)\.", text)
        return int(mm.group(1)) if mm else 0

    # "Available" the way Activity Monitor means it: not-wired, reclaimable memory.
    free = pages("Pages free")
    inactive = pages("Pages inactive")
    speculative = pages("Pages speculative")
    purgeable = pages("Pages purgeable")
    available = (free + inactive + speculative + purgeable) * page
    # Kernel's own pressure level when exposed: 1 normal, 2 warn, 4 critical.
    pressure = "unknown"
    lvl = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    if lvl in {"1", "2", "4"}:
        pressure = {"1": "normal", "2": "warn", "4": "critical"}[lvl]
    else:
        pressure = _pressure_from_fraction(available / total_bytes if total_bytes else 0.0)
    return float(available), pressure


def _windows_ram_total_and_available() -> tuple[float, float]:
    """Read physical RAM through the Windows API without adding a runtime dependency."""
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0.0, 0.0
        return float(status.ullTotalPhys), float(status.ullAvailPhys)
    except Exception:
        return 0.0, 0.0


def _linux_total_and_available_bytes() -> tuple[float, float]:
    total = available = 0.0
    with contextlib.suppress(OSError):
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = float(int(line.split()[1])) * 1024
                elif line.startswith("MemAvailable:"):
                    available = float(int(line.split()[1])) * 1024
    return total, available


def _pressure_from_fraction(free_fraction: float) -> str:
    if free_fraction <= 0.0:
        return "unknown"
    if free_fraction < 0.10:
        return "critical"
    if free_fraction < 0.20:
        return "warn"
    return "normal"


def _swap_used_bytes() -> float:
    system = platform.system()
    if system == "Darwin":
        # vm.swapusage: "total = 2048.00M  used = 512.00M  free = 1536.00M"
        text = _run(["sysctl", "-n", "vm.swapusage"])
        m = re.search(r"used\s*=\s*([\d.]+)([MGK])", text)
        if m:
            val, unit = float(m.group(1)), m.group(2)
            return val * {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[unit]
        return 0.0
    if system == "Linux":
        total = free = 0.0
        with contextlib.suppress(OSError):
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("SwapTotal:"):
                        total = float(int(line.split()[1])) * 1024
                    elif line.startswith("SwapFree:"):
                        free = float(int(line.split()[1])) * 1024
        return max(0.0, total - free)
    return 0.0


# ------------------------------------------------------------------ VOOL's own footprint

def _ollama_loaded_bytes() -> float:
    """Bytes Ollama is holding in memory now (sum of loaded models), via /api/ps. 0 if none/unreachable."""
    # The canonical LocalModelPolicy: disabled means no Ollama health probe either; report
    # no local footprint (0.0), the same truth as an unreachable daemon.
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return 0.0
    try:
        req = urllib.request.Request(_OLLAMA_PS_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    # Resource sensing is advisory. A local daemon, test guard, or platform-specific
    # transport can fail in ways other than a normal socket/JSON error; none of those
    # should take down a request that is merely asking for a snapshot.
    except Exception:
        return 0.0
    total = 0.0
    for model in data.get("models") or []:
        with contextlib.suppress(TypeError, ValueError):
            total += float(model.get("size") or 0)
    return total


def _comfyui_running() -> bool:
    out = _run(["pgrep", "-f", f"main.py --port {_COMFYUI_PORT}"])
    return bool(out.strip())


# ------------------------------------------------------------------ snapshot

def _build_snapshot() -> ResourceSnapshot:
    system = platform.system()
    if system == "Darwin":
        total = _macos_total_ram_bytes()
        available, pressure = _macos_ram_available_and_pressure(total)
    elif system == "Linux":
        total, available = _linux_total_and_available_bytes()
        pressure = _pressure_from_fraction(available / total if total else 0.0)
    elif system == "Windows":
        total, available = _windows_ram_total_and_available()
        pressure = _pressure_from_fraction(available / total if total else 0.0)
    else:
        total = available = 0.0
        pressure = "unknown"

    try:
        load_1m = float(os.getloadavg()[0])
    except (OSError, AttributeError):
        load_1m = 0.0
    cpu_count = os.cpu_count() or 1
    free_fraction = (available / total) if total else 0.0
    reserve = total * _reserve_fraction()
    usable = max(0.0, available - reserve)

    return ResourceSnapshot(
        total_ram_gb=total / _GIB,
        available_ram_gb=available / _GIB,
        used_ram_gb=max(0.0, total - available) / _GIB,
        free_fraction=free_fraction,
        memory_pressure=pressure,
        load_1m=load_1m,
        cpu_count=cpu_count,
        load_per_core=(load_1m / cpu_count) if cpu_count else load_1m,
        swap_used_gb=_swap_used_bytes() / _GIB,
        ollama_loaded_gb=_ollama_loaded_bytes() / _GIB,
        comfyui_running=_comfyui_running(),
        reserve_gb=reserve / _GIB,
        usable_gb=usable / _GIB,
    )


def snapshot(*, force: bool = False) -> ResourceSnapshot:
    """A live resource snapshot, cached for ~3s so it is free to read per-turn. ``force`` re-samples now."""
    global _cache
    now = time.monotonic()
    with _lock:
        if not force and _cache is not None and (now - _cache[0]) < _CACHE_TTL:
            return _cache[1]
    snap = _build_snapshot()
    with _lock:
        _cache = (time.monotonic(), snap)
    return snap


def summary_line(snap: ResourceSnapshot | None = None) -> str:
    """One-line human summary for a resources pill / diagnostics."""
    s = snap or snapshot()
    return (
        f"RAM {s.available_ram_gb:.1f}/{s.total_ram_gb:.1f} GB free "
        f"({s.memory_pressure}), keep {s.reserve_gb:.1f} GB for you → {s.usable_gb:.1f} GB usable · "
        f"load {s.load_per_core:.2f}/core · "
        f"Ollama {s.ollama_loaded_gb:.1f} GB · ComfyUI {'up' if s.comfyui_running else 'off'}"
    )


__all__ = ["ResourceSnapshot", "snapshot", "summary_line"]

"""Live resource sensing: parsing, the reserve/usable math, pressure thresholds, and fail-soft."""
from __future__ import annotations

import core.system_resources as sr

_GIB = 1024.0 ** 3

_VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              100000.
Pages active:                            300000.
Pages inactive:                          200000.
Pages speculative:                        50000.
Pages purgeable:                          50000.
"""


def _fake_run(cmd, timeout=2.0):
    joined = " ".join(cmd)
    if "hw.memsize" in joined:
        return "25769803776\n"                        # exactly 24 GiB
    if cmd[:1] == ["vm_stat"]:
        return _VM_STAT
    if "kern.memorystatus_vm_pressure_level" in joined:
        return "1\n"                                   # normal
    if "vm.swapusage" in joined:
        return "total = 4096.00M  used = 12680.00M  free = 0.00M\n"
    return ""


def _patch_macos(monkeypatch, *, ollama_gb=8.0, comfyui=True):
    monkeypatch.setattr(sr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sr, "_run", _fake_run)
    monkeypatch.setattr(sr, "_ollama_loaded_bytes", lambda: ollama_gb * _GIB)
    monkeypatch.setattr(sr, "_comfyui_running", lambda: comfyui)
    monkeypatch.setattr(sr.os, "getloadavg", lambda: (2.0, 3.0, 4.0), raising=False)
    monkeypatch.setattr(sr.os, "cpu_count", lambda: 10)
    sr._cache = None


def test_snapshot_reads_and_computes(monkeypatch) -> None:
    _patch_macos(monkeypatch)
    s = sr.snapshot(force=True)
    assert abs(s.total_ram_gb - 24.0) < 0.01                     # hw.memsize parsed
    # available = (free + inactive + speculative + purgeable) * 16384 = 400000 * 16384
    assert abs(s.available_ram_gb - (400000 * 16384 / _GIB)) < 0.01
    assert s.memory_pressure == "normal"                         # from pressure level 1
    assert abs(s.reserve_gb - 24.0 * 0.20) < 0.01                # 20% reserve for the user
    assert abs(s.usable_gb - max(0.0, s.available_ram_gb - s.reserve_gb)) < 0.01
    assert abs(s.ollama_loaded_gb - 8.0) < 0.01                  # VOOL's own footprint
    assert s.comfyui_running is True
    assert abs(s.swap_used_gb - (12680 / 1024)) < 0.01           # 12.38 GB swapped
    assert abs(s.load_per_core - 0.2) < 0.01                     # 2.0 / 10 cores


def test_pressure_thresholds() -> None:
    assert sr._pressure_from_fraction(0.05) == "critical"
    assert sr._pressure_from_fraction(0.15) == "warn"
    assert sr._pressure_from_fraction(0.50) == "normal"
    assert sr._pressure_from_fraction(0.0) == "unknown"


def test_pressure_falls_back_to_fraction_when_level_absent(monkeypatch) -> None:
    def run_no_level(cmd, timeout=2.0):
        if "kern.memorystatus_vm_pressure_level" in " ".join(cmd):
            return ""                                            # kernel level not exposed
        return _fake_run(cmd, timeout)
    _patch_macos(monkeypatch)
    monkeypatch.setattr(sr, "_run", run_no_level)
    s = sr.snapshot(force=True)
    assert s.memory_pressure in {"normal", "warn", "critical"}   # derived from free fraction, never "unknown" here


def test_fail_soft_when_tools_missing(monkeypatch) -> None:
    monkeypatch.setattr(sr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sr, "_run", lambda cmd, timeout=2.0: "")   # every probe empty
    monkeypatch.setattr(sr, "_ollama_loaded_bytes", lambda: 0.0)
    monkeypatch.setattr(sr, "_comfyui_running", lambda: False)
    sr._cache = None
    s = sr.snapshot(force=True)                                    # must not raise
    assert s.total_ram_gb == 0.0 and s.usable_gb == 0.0
    assert s.memory_pressure == "unknown"
    assert "RAM" in sr.summary_line(s)


def test_snapshot_is_cached(monkeypatch) -> None:
    _patch_macos(monkeypatch)
    calls = {"n": 0}
    real_build = sr._build_snapshot
    monkeypatch.setattr(sr, "_build_snapshot", lambda: (calls.__setitem__("n", calls["n"] + 1) or real_build()))
    sr._cache = None
    sr.snapshot(force=True)
    sr.snapshot()                                                 # within TTL -> cached, no rebuild
    assert calls["n"] == 1


def test_reserve_fraction_reads_user_pref(tmp_path, monkeypatch):
    """The RAM reserve honours the user's Settings preference (ram_reserve_pct), env override aside."""
    from core import runtime_paths, user_preferences

    monkeypatch.delenv("VOOL_RAM_RESERVE_FRACTION", raising=False)
    runtime_paths.configure_runtime_home(tmp_path / "home")
    try:
        assert abs(sr._reserve_fraction() - 0.20) < 1e-9          # default when unset
        prefs = user_preferences.load_preferences()
        prefs.ram_reserve_pct = 40
        user_preferences.save_preferences(prefs)
        assert user_preferences.load_preferences().ram_reserve_pct == 40   # persisted round-trip
        assert abs(sr._reserve_fraction() - 0.40) < 1e-9          # governor reads it
        prefs.ram_reserve_pct = 999                               # out of range -> clamped to 80 on save
        user_preferences.save_preferences(prefs)
        assert user_preferences.load_preferences().ram_reserve_pct == 80
    finally:
        runtime_paths.configure_runtime_home(None)

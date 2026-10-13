"""Known host inputs for provider transport fixtures; the actual load gate still decides."""
from core.system_resources import ResourceSnapshot


def fixture_host_snapshot(available_gb: float = 64.0) -> ResourceSnapshot:
    available = float(available_gb)
    total = max(68.0, available)
    return ResourceSnapshot(
        total_ram_gb=total, available_ram_gb=available, used_ram_gb=total-available,
        free_fraction=available/total, memory_pressure="normal", load_1m=0.0,
        cpu_count=8, load_per_core=0.0, swap_used_gb=0.0, ollama_loaded_gb=0.0,
        comfyui_running=False, reserve_gb=4.0, usable_gb=max(0.0, available-4.0),
    )


def pin_fixture_host_memory(monkeypatch, available_gb: float = 64.0) -> None:
    monkeypatch.setattr(
        "core.system_resources.snapshot",
        lambda **_kwargs: fixture_host_snapshot(available_gb),
    )
    monkeypatch.setattr("core.resource_governor.resident_models", lambda **_kwargs: [])

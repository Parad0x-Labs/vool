"""Known host inputs for provider transport fixtures; the actual load gate still decides."""
from types import SimpleNamespace


def pin_fixture_host_memory(monkeypatch, available_gb: float = 64.0) -> None:
    monkeypatch.setattr(
        "core.system_resources.snapshot",
        lambda **_kwargs: SimpleNamespace(available_ram_gb=float(available_gb)),
    )
    monkeypatch.setattr("core.resource_governor.resident_models", lambda: [])

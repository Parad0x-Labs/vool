from __future__ import annotations

from types import SimpleNamespace

from core.local_worker_pool import (
    _detect_available_vram_gb,
    recommend_local_worker_capacity,
    resolve_local_worker_capacity,
)


def test_recommend_local_worker_capacity_respects_hard_cap(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 64)
    value = recommend_local_worker_capacity(hard_cap=10)
    assert 1 <= value <= 10


def test_resolve_local_worker_capacity_uses_override() -> None:
    effective, recommended = resolve_local_worker_capacity(requested=12, hard_cap=10)
    assert effective == 12
    assert 1 <= recommended <= 10


def test_resolve_local_worker_capacity_auto_path(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    monkeypatch.setattr("core.local_worker_pool._detect_available_memory_gb", lambda: 16.0)
    monkeypatch.setattr("core.local_worker_pool._detect_available_vram_gb", lambda: 8.0)
    effective, recommended = resolve_local_worker_capacity(requested=None, hard_cap=10)
    assert effective == recommended
    assert effective == 4


def test_recommend_local_worker_capacity_respects_vram_limit(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 32)
    monkeypatch.setattr("core.local_worker_pool._detect_available_memory_gb", lambda: 64.0)
    monkeypatch.setattr("core.local_worker_pool._detect_available_vram_gb", lambda: 3.9)
    value = recommend_local_worker_capacity(hard_cap=10)
    assert value == 1


def test_available_vram_uses_bounded_nvidia_smi_without_importing_torch(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        assert kwargs["timeout"] == 3
        return SimpleNamespace(returncode=0, stdout="4096\n8192\n")

    monkeypatch.setattr("subprocess.run", fake_run)

    assert _detect_available_vram_gb() == 8.0
    assert calls == [
        [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ]
    ]


def test_available_vram_fails_soft_when_nvidia_smi_is_unavailable(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr("subprocess.run", unavailable)

    assert _detect_available_vram_gb() is None

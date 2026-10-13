"""Local-model budgets keep their measured GPU values and scale once on a CPU-only machine.

Intel acceptance run 38311523828 (i7-8700B, no GPU, qwen3:8b): the entity-ambiguity probe's fixed
35 s budget cancelled a call that needed ~34 s of prompt eval alone, so recall asked back instead
of answering. These pin the scaling rule and that the runtime's call sites go through it.
"""

from __future__ import annotations

import pytest

from core import local_compute_budget
from core.hardware_tier import MachineProbe


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    local_compute_budget.local_inference_is_cpu_only.cache_clear()
    yield
    local_compute_budget.local_inference_is_cpu_only.cache_clear()


def _machine(monkeypatch, accelerator: str) -> list[int]:
    calls: list[int] = []

    def probe() -> MachineProbe:
        calls.append(1)
        return MachineProbe(cpu_cores=8, ram_gb=16.0, gpu_name=None, vram_gb=None, accelerator=accelerator)

    monkeypatch.setattr("core.hardware_tier.probe_machine", probe)
    return calls


@pytest.mark.parametrize("accelerator", ["mps", "cuda", "directml"])
def test_a_gpu_machine_keeps_every_measured_budget(monkeypatch, accelerator):
    _machine(monkeypatch, accelerator)
    assert local_compute_budget.local_inference_is_cpu_only() is False
    for seconds in (35.0, 75.0, 25.0, 20.0):
        assert local_compute_budget.local_compute_seconds(seconds) == seconds


def test_a_cpu_only_machine_scales_each_budget_by_one_factor(monkeypatch):
    calls = _machine(monkeypatch, "cpu")
    assert local_compute_budget.local_compute_seconds(35.0) == 35.0 * local_compute_budget.CPU_ONLY_FACTOR
    assert local_compute_budget.local_compute_seconds(75.0) == 75.0 * local_compute_budget.CPU_ONLY_FACTOR
    # The measured Intel probe (~34 s prompt eval + ~6 s verdict) fits the scaled budget.
    assert local_compute_budget.local_compute_seconds(35.0) > 34.0 + 6.0
    # Probed once per process, not on every call.
    assert len(calls) == 1


def test_an_unreadable_hardware_probe_keeps_the_gpu_budget(monkeypatch):
    def broken() -> MachineProbe:
        raise OSError("system_profiler unavailable")

    monkeypatch.setattr("core.hardware_tier.probe_machine", broken)
    assert local_compute_budget.local_compute_seconds(35.0) == 35.0


def test_the_planner_phase_keeps_its_share_of_the_turn_on_a_cpu_only_machine(monkeypatch):
    from core.conductor.scheduler import DEFAULT_PLAN_DEADLINE_S, PLANNER_PHASE_CAP_S

    _machine(monkeypatch, "cpu")
    turn = local_compute_budget.local_compute_seconds(DEFAULT_PLAN_DEADLINE_S)
    planner = local_compute_budget.local_compute_seconds(PLANNER_PHASE_CAP_S)
    assert turn * 0.3 <= planner < turn * 0.5


def test_the_ambiguity_probe_and_conductor_deadlines_are_scaled_at_their_call_sites():
    """The three agent deadlines and the helper-call ceiling all read the one scaling rule."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "core" / "agent_runtime"
    agent = (root / "agent.py").read_text(encoding="utf-8")
    hook = (root / "turn_planner_hook.py").read_text(encoding="utf-8")
    assert "monotonic() + local_compute_seconds(DEFAULT_PLAN_DEADLINE_S)" in agent
    assert "monotonic() + local_compute_seconds(PLANNER_PHASE_CAP_S)" in agent
    assert "monotonic() + local_compute_seconds(35.0)" in agent
    assert "monotonic() + 35.0" not in agent
    assert "monotonic() + local_compute_seconds(timeout_seconds)" in hook

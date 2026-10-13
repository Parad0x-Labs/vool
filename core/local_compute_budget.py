"""Wall-clock budgets for local-model work, scaled once for machines with no GPU.

The runtime's fixed budgets (the conductor turn, its planner phase, the helper-call ceilings, the
entity-ambiguity probe) were measured on GPU hosts: Apple Silicon and CUDA. A machine with no
usable accelerator runs the same local model on its CPU, many times slower. Measured on a real
Intel Mac (i7-8700B, macOS 15.7, ollama v0.40.3, qwen3:8b; #168 run 38311523828, 2026-10-12):
prompt eval 8.7 tokens/s and generation 3.2 tokens/s, so the 296-token ambiguity probe needed
about 34 s before it could write a token. Its 35 s budget cancelled it, and every plain question,
memory recall included, asked back instead of answering.

`local_compute_seconds` is the one place that scaling happens. With a GPU the budget is returned
unchanged, so Apple Silicon and GPU Linux keep exactly the measured numbers. With none it is
multiplied by `CPU_ONLY_FACTOR`. The budgets are ceilings, not waits: a fast answer still returns
as soon as it is ready, and a turn that uses a cloud model on such a machine is only allowed
longer before a hung call is abandoned.
"""

from __future__ import annotations

from functools import lru_cache

#: Sized from the Intel measurement above: the ambiguity probe needs ~34 s of prompt eval plus
#: ~6 s to write its verdict on that CPU, against 35 s it was given; the warm second call reuses
#: the prompt cache. Four times the GPU budget (140 s for the probe, 300 s for a conductor turn)
#: covers that cold call with room for a slower CPU.
CPU_ONLY_FACTOR = 4.0


@lru_cache(maxsize=1)
def local_inference_is_cpu_only() -> bool:
    """True when this machine has no usable GPU for local inference. Probed once per process."""
    try:
        from core.hardware_tier import probe_machine

        return str(probe_machine().accelerator or "").strip().lower() == "cpu"
    except Exception:
        # An unreadable probe keeps the measured GPU budgets: never longer than proven needed.
        return False


def local_compute_seconds(seconds: float) -> float:
    """The budget for local-model work that was measured as ``seconds`` on a GPU host."""
    budget = float(seconds)
    return budget * CPU_ONLY_FACTOR if local_inference_is_cpu_only() else budget

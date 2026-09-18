from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_chat import _bootstrap_agent, _print_prewarm_results


def test_print_prewarm_results_renders_success_skip_and_failure(capsys) -> None:
    _print_prewarm_results(
        (
            {"ok": True, "provider_id": "ollama-local:qwen2.5:14b", "status": "prewarmed", "keep_alive": "15m"},
            {
                "ok": True,
                "provider_id": "ollama-local:qwen2.5:32b",
                "status": "timed_out",
                "keep_alive": "15m",
                "reason": "cold_start_timeout",
            },
            {"ok": True, "provider_id": "vllm-local:qwen2.5:32b", "status": "skipped", "reason": "not_ollama_runtime"},
            {"ok": False, "provider_id": "ollama-local:qwen2.5:7b", "status": "error", "error": "connection refused"},
        )
    )

    output = capsys.readouterr().out
    assert "Provider prewarm:" in output
    assert "ollama-local:qwen2.5:14b: prewarmed (keep_alive=15m)" in output
    assert (
        "ollama-local:qwen2.5:32b: prewarm timed out; continuing without background warming "
        "(keep_alive=15m, reason=cold_start_timeout)"
    ) in output
    assert "vllm-local:qwen2.5:32b: skipped (not_ollama_runtime)" in output
    assert "ollama-local:qwen2.5:7b: failed (connection refused)" in output


def test_bootstrap_agent_surfaces_prewarm_results(capsys) -> None:
    backbone = SimpleNamespace(
        local_model_profile=SimpleNamespace(
            probe=SimpleNamespace(accelerator="mps"),
            tier=SimpleNamespace(tier_name="mid", ollama_tag="qwen2.5:14b"),
            summary={"accelerator": "mps", "ram_gb": 24, "gpu": "Apple GPU", "vram_gb": 0},
        ),
        provider_snapshot=SimpleNamespace(
            warnings=tuple(),
            prewarm_results=(
                {"ok": True, "provider_id": "ollama-local:qwen2.5:14b", "status": "prewarmed", "keep_alive": "15m"},
            ),
        ),
        boot=SimpleNamespace(
            backend_selection=SimpleNamespace(
                backend_name="mlx",
                device="mps",
            )
        ),
    )
    compute_daemon = mock.Mock()
    compute_daemon.budget = SimpleNamespace(mode="active", cpu_threads=8, gpu_memory_fraction=0.7)
    agent = mock.Mock()
    agent.start.return_value = SimpleNamespace(backend_name="mlx", device="mps", persona_id="default")

    with mock.patch("apps.vool_chat.build_runtime_backbone", return_value=backbone), mock.patch(
        "apps.vool_chat.is_first_boot",
        return_value=False,
    ), mock.patch(
        "apps.vool_chat.ComputeModeDaemon",
        return_value=compute_daemon,
    ), mock.patch(
        "apps.vool_chat.VoolAgent",
        return_value=agent,
    ), mock.patch(
        "apps.vool_chat.get_agent_display_name",
        return_value="VOOL",
    ):
        booted_agent = _bootstrap_agent(persona_id="default", device="openclaw")

    assert booted_agent is agent
    output = capsys.readouterr().out
    assert "Provider prewarm:" in output
    assert "ollama-local:qwen2.5:14b: prewarmed (keep_alive=15m)" in output

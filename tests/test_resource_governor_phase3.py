"""Phase 3 model-load governance: fit planning, hog reporting, reclaim, the adapter gate, and
the "why is my Mac roaring / free up memory" chat surface."""
from __future__ import annotations

from unittest import mock

import core.resource_governor as gov
from core.agent_runtime.fast_paths_governor import (
    maybe_handle_free_memory_command,
    maybe_handle_resource_hog_question,
)


def _snap(available=10.0, total=24.0, pressure="normal", swap=1.0, comfy=False, ollama_gb=0.0):
    s = mock.Mock()
    s.total_ram_gb = total
    s.available_ram_gb = available
    s.memory_pressure = pressure
    s.swap_used_gb = swap
    s.load_per_core = 0.5
    s.comfyui_running = comfy
    s.ollama_loaded_gb = ollama_gb
    s.usable_gb = max(0.0, available - total * 0.2)
    return s


# ------------------------------------------------------------------ plan_model_load

def test_resident_model_is_never_gated() -> None:
    with (
        mock.patch.object(gov.sr, "snapshot", return_value=_snap(available=0.5)),
        mock.patch.object(gov, "resident_models", return_value=[{"name": "qwen3:8b", "size_gb": 5.2}]),
    ):
        decision = gov.plan_model_load("qwen3:8b")
    assert decision.ok is True  # serving a loaded model costs no new RAM


def test_load_that_fits_proceeds_without_reclaim() -> None:
    with (
        mock.patch.object(gov.sr, "snapshot", return_value=_snap(available=10.0)),
        mock.patch.object(gov, "resident_models", return_value=[]),
    ):
        decision = gov.plan_model_load("qwen3:8b")  # ~5.2 GB into 10 GB free
    assert decision.ok is True and decision.actions == []


def test_oversized_load_reclaims_others_then_rechecks() -> None:
    snaps = [_snap(available=3.0), _snap(available=12.0)]  # before reclaim, after reclaim
    with (
        mock.patch.object(gov.sr, "snapshot", side_effect=snaps),
        mock.patch.object(gov, "resident_models", return_value=[{"name": "vool-qwen3-30b-a3b:nothink", "size_gb": 16.8}]),
        mock.patch.object(gov, "_unload_models", return_value=["vool-qwen3-30b-a3b:nothink"]) as unload,
    ):
        decision = gov.plan_model_load("qwen3:8b")
    assert decision.ok is True
    unload.assert_called_once_with(["vool-qwen3-30b-a3b:nothink"])
    assert "unloaded" in decision.actions[0]


def test_load_that_cannot_fit_refuses_instead_of_freezing() -> None:
    with (
        mock.patch.object(gov.sr, "snapshot", return_value=_snap(available=2.0)),
        mock.patch.object(gov, "resident_models", return_value=[]),
    ):
        decision = gov.plan_model_load("qwen3:14b")  # ~9.3 GB into 2 GB free, nothing to reclaim
    assert decision.ok is False


def test_unknown_model_size_never_blocks() -> None:
    with (
        mock.patch.object(gov.sr, "snapshot", return_value=_snap(available=0.5)),
        mock.patch.object(gov, "resident_models", return_value=[]),
        mock.patch.object(gov, "estimated_model_gb", return_value=0.0),
    ):
        assert gov.plan_model_load("mystery:latest").ok is True


# ------------------------------------------------------------------ hog report + free

def test_hog_report_names_the_external_squeezer() -> None:
    with (
        mock.patch.object(gov.sr, "snapshot", return_value=_snap(available=2.1, pressure="warn", swap=12.3)),
        mock.patch.object(gov, "resident_models", return_value=[{"name": "qwen3.5:35b-a3b", "size_gb": 16.8}]),
    ):
        report = gov.resource_hog_report()
    assert report["squeezed"] is True
    assert report["hog"]["name"] == "qwen3.5:35b-a3b"
    assert report["hog_is_vool_routable"] is True  # it IS in the size table; the chat copy handles wording


def test_free_up_memory_reports_before_after() -> None:
    snaps = [_snap(available=2.0, comfy=True), _snap(available=18.5)]
    with (
        mock.patch.object(gov.sr, "snapshot", side_effect=snaps),
        mock.patch.object(gov, "resident_models", return_value=[{"name": "qwen3.5:35b-a3b", "size_gb": 16.8}]),
        mock.patch.object(gov, "_unload_models", return_value=["qwen3.5:35b-a3b"]),
        mock.patch.object(gov, "stop_comfyui", return_value=True),
    ):
        result = gov.free_up_memory()
    assert result["unloaded"] == ["qwen3.5:35b-a3b"]
    assert result["stopped_comfyui"] is True
    assert result["freed_gb"] == 16.5


# ------------------------------------------------------------------ chat surface

def test_roaring_question_gets_measured_answer() -> None:
    report = {
        "total_gb": 24.0, "available_gb": 2.1, "pressure": "warn", "swap_used_gb": 12.3,
        "load_per_core": 1.2, "comfyui_running": False,
        "resident_models": [{"name": "qwen3.5:35b-a3b", "size_gb": 16.8}],
        "hog": {"name": "qwen3.5:35b-a3b", "size_gb": 16.8},
        "hog_is_vool_routable": False, "squeezed": True,
    }
    with mock.patch("core.resource_governor.resource_hog_report", return_value=report):
        reply = maybe_handle_resource_hog_question("why is my mac roaring always", owner_local=True)
    assert reply is not None
    assert "qwen3.5:35b-a3b" in reply and "16.8" in reply
    assert "another app loaded it" in reply
    assert "free up memory" in reply


def test_roaring_patterns_match_and_chat_does_not() -> None:
    for text in ("why is my mac roaring always", "my imac is so slow", "fans are going crazy", "what is eating my ram?"):
        with mock.patch("core.resource_governor.resource_hog_report", return_value={
            "total_gb": 24.0, "available_gb": 15.0, "pressure": "normal", "swap_used_gb": 0.5,
            "load_per_core": 0.3, "comfyui_running": False, "resident_models": [], "hog": None,
            "hog_is_vool_routable": False, "squeezed": False,
        }):
            assert maybe_handle_resource_hog_question(text, owner_local=True) is not None, text
    for text in ("why is the sky blue", "tell me a joke", "my code is slow"):
        assert maybe_handle_resource_hog_question(text, owner_local=True) is None, text


def test_free_memory_command_runs_and_reports() -> None:
    with mock.patch("core.resource_governor.free_up_memory", return_value={
        "unloaded": ["qwen3.5:35b-a3b"], "stopped_comfyui": False,
        "available_before_gb": 2.0, "available_after_gb": 18.5, "freed_gb": 16.5,
    }):
        reply = maybe_handle_free_memory_command("free up memory pls", owner_local=True)
    assert reply is not None and "qwen3.5:35b-a3b" in reply and "18.5" in reply


def test_governor_surface_is_owner_only() -> None:
    assert "owner-only" in maybe_handle_resource_hog_question("why is my mac so slow", owner_local=False)
    assert "owner-only" in maybe_handle_free_memory_command("free up memory", owner_local=False)


# ------------------------------------------------------------------ adapter gate

def test_adapter_gate_raises_clear_error_when_load_cannot_fit() -> None:
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    adapter = mock.Mock(spec=OpenAICompatibleAdapter)
    adapter.manifest = mock.Mock(model_name="qwen3:14b", provider_id="ollama-local:qwen3:14b")
    bad = gov.GovernorDecision(ok=False, footprint_gb=9.3, usable_before_gb=2.0, usable_after_gb=2.0)
    with mock.patch("core.resource_governor.plan_model_load", return_value=bad):
        try:
            OpenAICompatibleAdapter._gate_local_model_load(adapter)
            raise AssertionError("expected the gate to refuse")
        except RuntimeError as exc:
            assert "model_load_gated_low_memory" in str(exc)


def test_adapter_gate_fails_soft_on_governor_error() -> None:
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    adapter = mock.Mock(spec=OpenAICompatibleAdapter)
    adapter.manifest = mock.Mock(model_name="qwen3:8b", provider_id="ollama-local:qwen3:8b")
    with mock.patch("core.resource_governor.plan_model_load", side_effect=RuntimeError("boom")):
        OpenAICompatibleAdapter._gate_local_model_load(adapter)  # must not raise

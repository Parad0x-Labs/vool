"""The fast-local default must fit the machine — never pin a 19GB 30B on a 24GB box (memory thrash)."""

from __future__ import annotations

from types import SimpleNamespace

import core.runtime_provider_defaults as rpd


def _probe(ram_gb: float):
    return SimpleNamespace(ram_gb=ram_gb, accelerator="mps", vram_gb=ram_gb)


def test_fits_hardware_respects_ram_threshold(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_FAST_DEFAULT_MIN_RAM_GB", raising=False)
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(24.0))
    assert rpd._fast_default_fits_hardware() is False  # 24 GB < 32 GB default -> too small
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(64.0))
    assert rpd._fast_default_fits_hardware() is True
    # unknown RAM keeps prior behavior (don't over-restrict)
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(0.0))
    assert rpd._fast_default_fits_hardware() is True


def test_preferred_fast_model_gated_on_fit(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_ALLOW_OLLAMA_TAGS_FOR_DEFAULT", "1")
    monkeypatch.delenv("VOOL_FAST_DEFAULT_MIN_RAM_GB", raising=False)
    monkeypatch.setattr(rpd, "installed_ollama_model_names", lambda **k: (rpd._FAST_LOCAL_DEFAULT_MODEL,))
    # 24 GB: the big default is refused, caller falls through to the hardware-aware recommendation.
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(24.0))
    assert rpd.preferred_fast_local_model() == ""
    # 64 GB: the big default is fine.
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(64.0))
    assert rpd.preferred_fast_local_model() == rpd._FAST_LOCAL_DEFAULT_MODEL


def test_min_ram_env_override(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_FAST_DEFAULT_MIN_RAM_GB", "16")
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(24.0))
    assert rpd._fast_default_fits_hardware() is True  # 24 >= 16


def test_context_bucket_derived_from_ram(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_CONTEXT_BUCKET", raising=False)
    for ram, bucket in [(64.0, "E"), (24.0, "D"), (16.0, "C"), (10.0, "B"), (4.0, "A")]:
        monkeypatch.setattr("core.hardware_tier.probe_machine", lambda ram=ram: _probe(ram))
        assert rpd._hardware_context_bucket() == bucket
    # explicit override wins
    monkeypatch.setenv("VOOL_CONTEXT_BUCKET", "C")
    assert rpd._hardware_context_bucket() == "C"


def test_empty_bucket_sizes_from_hardware_not_4k(monkeypatch) -> None:
    # A capable box with no persisted bucket must NOT be pinned to the 4K baseline (the bug that
    # dropped earlier chat turns once a conversation routed back to the local model).
    monkeypatch.delenv("VOOL_CONTEXT_BUCKET", raising=False)
    monkeypatch.setenv("VOOL_ADAPTIVE_CONTEXT", "1")
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(24.0))
    ctx = rpd._ollama_context_sizing("daily", model_tag="qwen3:8b", bucket="")["selected_num_ctx"]
    assert ctx > 4096
    # a small box still lands at the safe 4K
    monkeypatch.setattr("core.hardware_tier.probe_machine", lambda: _probe(6.0))
    assert rpd._ollama_context_sizing("daily", model_tag="qwen3:8b", bucket="")["selected_num_ctx"] == 4096

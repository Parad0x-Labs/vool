"""A model the resource governor refused to LOAD stops riding first.

Measured 2026-11-05 (operator's live 0.7.8 session): an 8 GB model sat at the front of the
daily lane; the resource governor refused its LOAD every turn (`model_load_gated_low_memory`,
needs ~9.5 GB total with the headroom floor, machine stayed tight) — every turn paid the
refusal before falling back to the model that could serve. RAM pressure is semi-persistent:
the refusal is recorded in-process for a short window and the benched model moves to the BACK
of the ranked list. Advisory: it keeps its candidateship (RAM frees up), nothing persists
across processes, and a benched model is never removed.
"""

from __future__ import annotations

import time as _time
from types import SimpleNamespace

from core.memory_first_router import (
    _RAM_GATE_COOLDOWN,
    RAM_GATE_COOLDOWN_S,
    _demote_ram_gated,
    _note_ram_gate_refusal,
    _ram_gate_active,
)


def _manifest(provider_id: str) -> SimpleNamespace:
    return SimpleNamespace(provider_id=provider_id, model_name=provider_id.split(":", 1)[-1])


def test_ram_gate_refusal_benches_the_model() -> None:
    _RAM_GATE_COOLDOWN.pop("ollama-local:qwen2.5:7b", None)
    try:
        assert _ram_gate_active("ollama-local:qwen2.5:7b") is False
        _note_ram_gate_refusal("ollama-local:qwen2.5:7b")
        assert _ram_gate_active("ollama-local:qwen2.5:7b") is True
        ranked = [_manifest("ollama-local:qwen2.5:7b"), _manifest("ollama-local:qwen3:8b")]
        ordered = _demote_ram_gated(ranked)
        assert [m.provider_id for m in ordered] == [
            "ollama-local:qwen3:8b",
            "ollama-local:qwen2.5:7b",
        ], "the benched model rides last; the servable model first"
    finally:
        _RAM_GATE_COOLDOWN.pop("ollama-local:qwen2.5:7b", None)


def test_benched_model_is_never_removed() -> None:
    """Advisory only: a sole benched candidate still keeps its place in the list."""
    key = "ollama-local:solo"
    _RAM_GATE_COOLDOWN.pop(key, None)
    try:
        _note_ram_gate_refusal(key)
        sole = [_manifest(key)]
        assert [m.provider_id for m in _demote_ram_gated(sole)] == [key]
    finally:
        _RAM_GATE_COOLDOWN.pop(key, None)


def test_cooldown_expires() -> None:
    key = "ollama-local:expiring"
    _RAM_GATE_COOLDOWN.pop(key, None)
    try:
        _note_ram_gate_refusal(key, now=_time.monotonic() - RAM_GATE_COOLDOWN_S - 5.0)
        assert _ram_gate_active(key) is False
        assert _demote_ram_gated([_manifest(key), _manifest("other")])[0].provider_id == key
    finally:
        _RAM_GATE_COOLDOWN.pop(key, None)


def test_window_is_finite_and_short() -> None:
    assert 30 <= RAM_GATE_COOLDOWN_S <= 600


def test_relative_order_of_healthy_models_is_untouched() -> None:
    _RAM_GATE_COOLDOWN.pop("benched", None)
    try:
        _note_ram_gate_refusal("benched")
        a, b, c = _manifest("a"), _manifest("b"), _manifest("benched")
        ranked = [a, b, c]
        ordered = _demote_ram_gated(ranked)
        assert [m.provider_id for m in ordered] == ["a", "b", "benched"]
    finally:
        _RAM_GATE_COOLDOWN.pop("benched", None)

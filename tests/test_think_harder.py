"""Opt-in 'think harder' wrapper: off => one call (unchanged), on => self-consistency mux pick.

The sampler is a stub — no live model is called.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.think_harder import (
    mux_models,
    think_harder_answer,
    think_harder_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VOOL_SLM_MUX_MODE", raising=False)
    monkeypatch.delenv("VOOL_SLM_MUX_MODELS", raising=False)
    yield


class Sampler:
    """Records calls; per-model answers may be a fixed string or a per-call list (by call order)."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model: str, prompt: str) -> str:
        seen = sum(1 for m, _ in self.calls if m == model)
        self.calls.append((model, prompt))
        val = self.answers.get(model, "")
        if isinstance(val, list):
            return val[seen % len(val)]
        return val


# --- disabled by default (single call, unchanged behaviour) ----------------

def test_disabled_makes_one_call() -> None:
    s = Sampler({"qwen2.5:7b": "hello"})
    res = think_harder_answer("hi", s)
    assert res.answer == "hello" and res.used_mux is False
    assert res.model == "qwen2.5:7b"
    assert len(s.calls) == 1  # exactly one sample when off


def test_disabled_uses_first_model() -> None:
    s = Sampler({"a": "AA"})
    res = think_harder_answer("q", s, models=["a", "b", "c"])
    assert res.model == "a" and res.answer == "AA" and res.used_mux is False
    assert all(m == "a" for m, _ in s.calls)


def test_think_harder_enabled_reads_flag(monkeypatch) -> None:
    assert think_harder_enabled() is False
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "on")
    assert think_harder_enabled() is True


# --- model list resolution -------------------------------------------------

def test_mux_models_default() -> None:
    assert mux_models() == ("qwen2.5:7b", "qwen2.5:3b")


def test_mux_models_from_env(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODELS", " a , b ,c ")
    assert mux_models() == ("a", "b", "c")


def test_mux_models_explicit_arg_overrides_default() -> None:
    assert mux_models(["x", "y"]) == ("x", "y")


# --- enabled (routes through the self-consistency mux) ---------------------

def test_enabled_routes_through_mux(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    s = Sampler({"a": "The answer is 42", "b": "The answer is 42"})
    res = think_harder_answer("q", s, models=["a", "b"], k=3)
    assert res.used_mux is True
    assert "42" in res.answer
    assert len(s.calls) == 6  # 2 models x k=3 samples


def test_enabled_picks_more_self_consistent_model(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "yes")
    # 'a' is perfectly self-consistent; 'b' disagrees with itself every sample.
    s = Sampler({"a": "answer: 42", "b": ["1", "2", "3"]})
    res = think_harder_answer("q", s, models=["a", "b"], k=3)
    assert res.used_mux is True
    assert res.model == "a"
    assert res.confidence == pytest.approx(1.0)

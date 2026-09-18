"""Gauntlet — category 2 (part B): L2 summarizer caching + model eligibility.

Deterministic, no live model. These pin the two properties that
test_memory_compression_structure.py cannot see, because it never counts model
calls and never exercises model selection:

  - the summary is computed once per anchored range, not once per turn. The
    compressed window slides forward every turn, so a key over the exact range
    never repeats; compress_if_needed anchors the summarized prefix to a stride
    boundary so consecutive turns reuse one summary.
  - a model too small to be trusted with verbatim facts is never selected, and
    when nothing installed qualifies the summarizer takes its deterministic
    extractive fallback rather than a fabricating model.

The structure contract from part A (len == KEEP_RECENT + 1) is re-asserted here
under the anchored path, since the carried remainder must not change the shape.
"""
from __future__ import annotations

import json

import pytest

import core.conversation_summarizer as cs
from core.conversation_summarizer import KEEP_RECENT, compress_if_needed, reset_summary_cache

pytestmark = [pytest.mark.gauntlet]

CANNED_SUMMARY = (
    "## Key Facts\n- staging port 8791\n## Decisions Made\n- use postgres\n"
    "## Open Questions\n- None\n## Context Summary\nDiscussed the deploy plan."
)


@pytest.fixture(autouse=True)
def _clean_summary_cache():
    reset_summary_cache()
    yield
    reset_summary_cache()


@pytest.fixture
def call_counter(monkeypatch):
    calls: list[str] = []

    def counting(model, messages, timeout=60):
        calls.append(model)
        return CANNED_SUMMARY

    monkeypatch.setattr(cs, "_call_ollama", counting)
    return calls


def _history(n: int) -> list[dict]:
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"message number {i} with some real content to compress"})
    return out


# ---------------------------------------------------------------------------
# #17 — one summary per anchored range, not one per turn
# ---------------------------------------------------------------------------

def test_consecutive_turns_reuse_one_summary(call_counter):
    history = _history(24)
    for turn in range(6):
        history.append({"role": "user", "content": f"new question at turn {turn}"})
        history.append({"role": "assistant", "content": f"answer at turn {turn}"})
        compress_if_needed(history, model="test-model")
    # 6 turns add 12 messages; with stride 8 the anchor advances at most twice.
    assert len(call_counter) < 6, f"summary recomputed every turn: {len(call_counter)} calls in 6 turns"
    assert len(call_counter) <= 2


def test_repeated_compression_of_the_same_history_calls_the_model_once(call_counter):
    history = _history(30)
    for _ in range(5):
        compress_if_needed(history, model="test-model")
    assert len(call_counter) == 1


def test_a_different_conversation_is_not_served_a_cached_summary(call_counter):
    compress_if_needed(_history(30), model="test-model")
    other = [{"role": "user", "content": f"completely different topic {i}"} for i in range(30)]
    compress_if_needed(other, model="test-model")
    assert len(call_counter) == 2


def test_anchoring_preserves_the_compressed_shape(call_counter):
    # The carried (un-anchored) remainder rides inside the summary turn, so the
    # returned length must still be the part A contract: summary + keep_recent.
    for extra in range(1, 12):
        out, changed = compress_if_needed(_history(21 + extra), model="test-model")
        assert changed is True
        assert len(out) == KEEP_RECENT + 1, f"shape broke at {21 + extra} messages"


def test_carried_remainder_is_verbatim_not_paraphrased(call_counter):
    history = _history(30)
    out, _ = compress_if_needed(history, model="test-model")
    summary_turn = out[0]["content"]
    # split=22, anchor=16 -> messages 16..21 are carried verbatim into the summary turn
    assert "message number 21 with some real content to compress" in summary_turn
    assert CANNED_SUMMARY in summary_turn


def test_stride_one_reproduces_the_uncached_per_turn_behaviour(call_counter):
    # Guards the fix itself: with no anchoring the key never repeats and every
    # turn pays for a fresh summary. This is what stride > 1 exists to prevent.
    history = _history(24)
    for turn in range(4):
        history.append({"role": "user", "content": f"new question at turn {turn}"})
        history.append({"role": "assistant", "content": f"answer at turn {turn}"})
        compress_if_needed(history, model="test-model", stride=1)
    assert len(call_counter) == 4


# ---------------------------------------------------------------------------
# #18 — never summarize with a model too small to be faithful
# ---------------------------------------------------------------------------

def _tags(monkeypatch, models: list[tuple[str, str]]):
    payload = {"models": [{"name": n, "details": {"parameter_size": p}} for n, p in models]}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(cs, "_default_box_model", lambda: "")


def test_a_sub_threshold_model_is_never_picked(monkeypatch):
    _tags(monkeypatch, [("qwen3:0.6b", "751.63M"), ("qwen2.5:7b", "7.6B")])
    reset_summary_cache()
    assert cs._pick_model() == "qwen2.5:7b"


def test_only_tiny_models_installed_yields_no_model(monkeypatch):
    _tags(monkeypatch, [("qwen3:0.6b", "751.63M"), ("nomic-embed-text:latest", "137M")])
    reset_summary_cache()
    assert cs._pick_model() == ""


def test_no_eligible_model_falls_back_to_extraction_not_fabrication(monkeypatch):
    _tags(monkeypatch, [("qwen3:0.6b", "751.63M")])
    reset_summary_cache()

    def explode(*a, **k):
        raise AssertionError("summarizer called a model below the fidelity floor")

    monkeypatch.setattr(cs, "_call_ollama", explode)
    out = cs.summarize_messages([{"role": "user", "content": "The staging port is 8791 exactly"}])
    assert "8791" in out
    assert "## Key Facts" in out


def test_the_box_default_tag_wins_when_it_is_eligible(monkeypatch):
    _tags(monkeypatch, [("qwen2.5:7b", "7.6B"), ("qwen3:8b", "8.2B")])
    monkeypatch.setattr(cs, "_default_box_model", lambda: "qwen3:8b")
    reset_summary_cache()
    assert cs._pick_model() == "qwen3:8b"


def test_the_box_default_tag_is_ignored_when_it_is_too_small(monkeypatch):
    _tags(monkeypatch, [("qwen3:0.6b", "751.63M"), ("qwen2.5:7b", "7.6B")])
    monkeypatch.setattr(cs, "_default_box_model", lambda: "qwen3:0.6b")
    reset_summary_cache()
    assert cs._pick_model() == "qwen2.5:7b"


@pytest.mark.parametrize(
    "raw,expected",
    [("7.6B", 7.6), ("751.63M", 0.75163), ("137M", 0.137), ("14.8B", 14.8),
     ("", 0.0), (None, 0.0), ("weird", 0.0)],
)
def test_parameter_size_parsing(raw, expected):
    assert cs._parse_param_count_b(raw) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Extractive fallback must not corrupt values it cannot summarize
# ---------------------------------------------------------------------------

def test_extractive_fallback_does_not_split_decimals():
    # The old fallback took content.split(".")[0], turning "version 3.5" into
    # "version 3" — a dropped fact became a wrong one.
    out = cs._extractive_summary(
        [{"role": "user", "content": "We pinned version 3.5 and the rate is 99.99 percent."}],
        reason="test",
    )
    assert "3.5" in out
    assert "99.99" in out

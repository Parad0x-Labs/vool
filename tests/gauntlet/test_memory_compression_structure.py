"""Gauntlet — category 2 (part A): L2 compression structure (deterministic).

The L2 compressor (core.conversation_summarizer) was grep-confirmed to have no
tests. These pin its *structure and boundaries* with no live model:

  - compress_if_needed only fires above the threshold, and reduces to a single
    <context_summary> turn plus exactly keep_recent verbatim tail messages, with
    any system prefix preserved ahead of the summary.
  - the recent tail is byte-identical to the originals (recent turns are never
    paraphrased).
  - token_estimate is the pure chars//4 budget function.
  - when the model is unreachable (always, under the pytest socket guard) the
    summarizer takes its deterministic fallback and still emits the four-section
    shape, preserving the first sentence of each compressed message.

What is NOT here (needs the live lane): whether the *model-generated* summary
keeps an exact planted value verbatim. Note the fallback splits on ".", so it too
corrupts decimals — reinforcing that exact values belong in char-exact L3 memory
(see test_memory_and_needle.py), not in a summary.
"""
from __future__ import annotations

import pytest

from core.conversation_summarizer import (
    KEEP_RECENT,
    SUMMARY_THRESHOLD,
    compress_if_needed,
    summarize_messages,
    token_estimate,
)

pytestmark = [pytest.mark.gauntlet]

CANNED_SUMMARY = (
    "## Key Facts\n- staging port 8096\n## Decisions Made\n- use postgres\n"
    "## Open Questions\n- None\n## Context Summary\nDiscussed the deploy plan."
)


def _msgs(n, *, system=False):
    out = []
    if system:
        out.append({"role": "system", "content": "You are VOOL."})
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"message number {i} with some real content to compress"})
    return out


def _canned(monkeypatch):
    monkeypatch.setattr(
        "core.conversation_summarizer._call_ollama",
        lambda model, messages, timeout=60: CANNED_SUMMARY,
    )


# ---------------------------------------------------------------------------
# Threshold + structure
# ---------------------------------------------------------------------------

def test_at_or_below_threshold_is_not_compressed():
    msgs = _msgs(SUMMARY_THRESHOLD)  # exactly the threshold -> strict '>' means no compression
    out, changed = compress_if_needed(msgs, model="test-model")
    assert changed is False
    assert out == msgs


def test_above_threshold_reduces_to_summary_plus_keep_recent(monkeypatch):
    _canned(monkeypatch)
    msgs = _msgs(SUMMARY_THRESHOLD + 10)  # 30 messages, no system prefix
    out, changed = compress_if_needed(msgs, model="test-model")

    assert changed is True
    assert len(out) == KEEP_RECENT + 1                 # one summary turn + keep_recent tail
    assert out[0]["role"] == "assistant"
    assert "<context_summary>" in out[0]["content"]
    assert CANNED_SUMMARY in out[0]["content"]


def test_recent_tail_is_kept_byte_identical(monkeypatch):
    _canned(monkeypatch)
    msgs = _msgs(SUMMARY_THRESHOLD + 12)
    out, _ = compress_if_needed(msgs, model="test-model")
    # the newest keep_recent messages must survive compression verbatim
    assert out[-KEEP_RECENT:] == msgs[-KEEP_RECENT:]


def test_system_prefix_is_preserved_ahead_of_the_summary(monkeypatch):
    _canned(monkeypatch)
    msgs = _msgs(SUMMARY_THRESHOLD + 10, system=True)
    out, changed = compress_if_needed(msgs, model="test-model")

    assert changed is True
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "assistant" and "<context_summary>" in out[1]["content"]
    assert len(out) == KEEP_RECENT + 2                 # system + summary + keep_recent


# ---------------------------------------------------------------------------
# Pure budget function
# ---------------------------------------------------------------------------

def test_token_estimate_is_total_chars_over_four():
    msgs = [{"role": "user", "content": "a" * 40}, {"role": "assistant", "content": "b" * 40}]
    assert token_estimate(msgs) == 80 // 4


def test_token_estimate_ignores_non_content_keys():
    assert token_estimate([{"role": "user", "content": "abcd"}]) == 1


# ---------------------------------------------------------------------------
# Deterministic fallback (model unreachable under the pytest socket guard)
# ---------------------------------------------------------------------------

def test_fallback_emits_the_four_section_shape():
    out = summarize_messages([{"role": "user", "content": "The staging port is set and ready"}], model="test-model")
    assert "## Key Facts" in out
    assert "## Decisions Made" in out
    assert "## Open Questions" in out
    assert "## Context Summary" in out


def test_fallback_preserves_first_sentence_content():
    out = summarize_messages(
        [{"role": "user", "content": "Deploy target is Windows only. Everything else is secondary."}],
        model="test-model",
    )
    assert "Deploy target is Windows only" in out


def test_empty_messages_summarize_to_empty_string():
    assert summarize_messages([], model="test-model") == ""

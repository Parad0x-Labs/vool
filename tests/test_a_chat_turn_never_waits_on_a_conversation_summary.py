"""A chat turn never waits on a conversation summary being written.

Measured 2026-11-24 on sls's iMac (24 GB, qwen3:4b summarizing): one mail turn waited 47 s, 44 s and
48 s on three summaries of its older messages, 140 s of a 400 s turn, before any model answered. The
summary is now written off the turn, one at a time; a turn it is not ready for carries the newest older
messages verbatim, and a later turn reads the finished summary from the cache.
"""
from __future__ import annotations

import threading
import time

import pytest

import core.conversation_summarizer as cs
from core.conversation_summarizer import compress_if_needed, reset_summary_cache

SUMMARY = "## Key Facts\n- the staging port is 8791\n## Decisions Made\n- None\n## Open Questions\n- None\n## Context Summary\nx"


@pytest.fixture(autouse=True)
def _slow_model(monkeypatch):
    reset_summary_cache()
    release = threading.Event()
    calls: list[str] = []

    def slow(model, messages, timeout=60):
        calls.append(model)
        release.wait(10)
        return SUMMARY

    monkeypatch.setattr(cs, "_call_ollama", slow)
    monkeypatch.setattr(cs, "SUMMARY_TURN_WAIT_S", 0.2)
    yield calls, release
    release.set()
    deadline = time.time() + 5
    from core.off_turn import still_blocked

    while still_blocked("conversation-summary") and time.time() < deadline:
        time.sleep(0.02)
    reset_summary_cache()


def _history(n: int, tag: str = "") -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{tag}message {i} about the deploy plan"}
            for i in range(n)]


def _summary_block(messages: list[dict]) -> str:
    return str(messages[0]["content"])


def test_the_turn_goes_on_while_the_summary_is_written_and_the_next_turn_reads_it(_slow_model):
    calls, release = _slow_model
    history = _history(30)
    started = time.monotonic()
    first, compressed = compress_if_needed(history, model="m")
    assert compressed and time.monotonic() - started < 2.0
    block = _summary_block(first)
    assert "summary still being written" in block and "message 21" in block and "8791" not in block

    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and "8791" not in _summary_block(compress_if_needed(history, model="m")[0]):
        time.sleep(0.02)
    assert "8791" in _summary_block(compress_if_needed(history, model="m")[0])
    assert calls == ["m"]


def test_only_one_summary_is_written_at_a_time(_slow_model):
    calls, _release = _slow_model
    compress_if_needed(_history(30, "a "), model="m")
    second, _ = compress_if_needed(_history(30, "b "), model="m")
    assert "summary still being written" in _summary_block(second)
    assert len(calls) == 1


def test_the_verbatim_stand_in_stays_within_its_budget(_slow_model):
    long = [{"role": "user", "content": "word " * 400} for _ in range(60)]
    first, _ = compress_if_needed(long, model="m")
    assert len(_summary_block(first)) < 4000

"""Gauntlet_live — does the REAL summarizer keep the facts it compressed?

Every other L2 compression test monkeypatches `_call_ollama` with a canned
string, so a green default lane says nothing about whether a planted fact
survives a real round-trip — which is exactly how the summarizer shipped
preferring a model that invents values. These plant distinctive facts, run the
real model through `compress_if_needed`, and assert on the summary it produced.

Two directions, both of which the 0.75B model failed on-box:
  - RETENTION: a planted port / codename / datastore must survive compression.
  - FABRICATION: every number in the summary must appear in the source. A model
    that invents a value is worse than one that drops it — the invention re-enters
    the next turn as session history and reads exactly like a real fact.

Runs ONLY on-box under VOOL_ALLOW_LIVE_OLLAMA_TESTS=1 (or VOOL_ALPHA_LIVE_SOAK=1);
skips in the default CI lane.

    py -m pytest tests/gauntlet -m gauntlet_live -q   # with the env flag set
"""
from __future__ import annotations

import re

import pytest

from core.conversation_summarizer import _pick_model, reset_summary_cache, summarize_messages
from tests.gauntlet._live import LIVE_GATE, require_live_provider

pytestmark = [pytest.mark.gauntlet_live, LIVE_GATE]

PLANTED = (
    ("port 8791", "8791"),
    ("codename BLUEHERON", "BLUEHERON"),
    ("datastore PostgreSQL", "PostgreSQL"),
)


@pytest.fixture(autouse=True)
def _clean_summary_cache():
    reset_summary_cache()
    yield
    reset_summary_cache()


def _planted_range() -> list[dict]:
    """The message range compression actually hands to the model.

    Deliberately summarize_messages() and not compress_if_needed(): the latter
    anchors the summarized prefix to a stride boundary, which shrinks the model's
    input and masks fabrication (0.6b was measured clean through the anchored path
    at this history length but 4/8 defective on the raw range). This lane exists to
    probe the MODEL, so it feeds the raw range.
    """
    msgs = [
        {"role": "user", "content": "The staging service listens on port 8791."},
        {"role": "assistant", "content": "Understood, staging is on port 8791."},
        {"role": "user", "content": "Our internal project codename is BLUEHERON."},
        {"role": "assistant", "content": "Noted the codename BLUEHERON."},
        {"role": "user", "content": "We decided to use PostgreSQL for the datastore."},
        {"role": "assistant", "content": "PostgreSQL it is."},
    ]
    # Numbered filler: the fabrication fuel the 0.75B model turned into invented
    # ranges ("Message number: 18-30") and filed under "Decisions Made".
    for i in range(16):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"message number {i} with some real content to compress"})
    return msgs


def _live_summary() -> str:
    summary = summarize_messages(_planted_range())
    if "extractive fallback" in summary:
        pytest.fail(
            "the live lane fell back to extraction — the model call failed or no eligible "
            f"model is installed (picked {_pick_model()!r}); this run proves nothing about fidelity"
        )
    return summary


def test_live_summary_keeps_every_planted_fact():
    require_live_provider()
    summary = _live_summary()
    lost = [label for label, needle in PLANTED if needle.lower() not in summary.lower()]
    assert not lost, f"compression dropped {lost}\n---\n{summary}"


def test_live_summary_invents_no_number_absent_from_the_source():
    require_live_provider()
    history = _planted_range()
    source_numbers = set(re.findall(r"\d+", " ".join(str(m["content"]) for m in history)))
    summary = _live_summary()
    invented = sorted({n for n in re.findall(r"\d+", summary) if n not in source_numbers})
    assert not invented, f"summary invented number(s) {invented} not present in the source\n---\n{summary}"


def test_live_summary_does_not_over_redact_a_plain_codename():
    require_live_provider()
    summary = _live_summary()
    # BLUEHERON is a codename, not a secret. A model that blanket-redacts it has
    # destroyed a recoverable fact.
    assert "BLUEHERON" in summary, f"plain codename was redacted away\n---\n{summary}"


def test_the_picked_model_clears_the_fidelity_floor():
    require_live_provider()
    picked = _pick_model()
    assert picked, "no installed model cleared the summarizer fidelity floor"
    assert picked != "qwen3:0.6b", "picked the model measured to fabricate on this exact input"

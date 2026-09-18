"""A model may not stamp its own guesses "verified". Only the runtime may, and only on evidence.

Measured 2026-08-05: a multi-part car question that explicitly said "separate verified facts from
unavailable data, and do not guess" was answered with a **"Verified fact"** column and every cell
filled -- four with no source. One cell was internally contradictory (`1.6L 1ZR-FAE / 2ZR-FAE`
names a 1.8L/1797cc engine); another appears to fuse Toyota's 22.65M cumulative figure with its
1.22M 2013-annual figure into an invented 1997 peak.

This is protection (A) of the tiered-truthfulness design: the cheapest of four, pure
post-processing, and the one that alone stops such a table being read as sourced. It judges the
LABEL, never the claim -- deciding whether 2ZR-FAE is 1.8L needs authoritative data this layer does
not have, and a second model's recall is not verification.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.verification_labels import downgrade_unearned_verification_labels

COROLLA_TABLE = (
    "| Attribute | Verified fact |\n"
    "| --- | --- |\n"
    "| Most common engine | 1.6L 1ZR-FAE / 2ZR-FAE family |\n"
    "| Most common colour | White |\n"
)


def test_the_measured_corolla_table_loses_its_verified_column() -> None:
    out, downgraded = downgrade_unearned_verification_labels(COROLLA_TABLE)
    assert "Verified fact" not in out, "the model kept a label only the runtime may award"
    assert "Model-provided" in out
    assert downgraded, "the downgrade must be reported, not silent"


@pytest.mark.parametrize(
    "label",
    ["Verified fact", "Verified facts", "Confirmed fact", "fact-checked", "fact checked",
     "Proven fact", "authoritative figure", "officially verified"],
)
def test_every_self_awarded_label_is_downgraded(label) -> None:
    out, downgraded = downgrade_unearned_verification_labels(f"| {label} | White |")
    assert label.lower() not in out.lower(), f"{label!r} survived"
    assert downgraded


def test_the_reader_is_told_the_label_changed() -> None:
    """Silently relabelling would trade one honesty failure for another -- the reader would take the
    rewritten table as runtime-endorsed."""
    out, _ = downgrade_unearned_verification_labels(COROLLA_TABLE)
    assert "set by the model, not verified by the runtime" in out


@pytest.mark.parametrize(
    "text",
    [
        "Toyota officially reported more than 50 million Corolla sales in 2021.",
        "Volkswagen confirmed the Golf passed 37 million units.",
        "Bitcoin is $64,238.00 USD as of 2026-08-05. Source: [CoinGecko](https://coingecko.com).",
        "The official figure comes from the manufacturer's annual report.",
        "I couldn't find a verified source for the colour breakdown.",
        "",
        "   ",
    ],
)
def test_ordinary_prose_and_runtime_sourced_answers_are_untouched(text) -> None:
    """A statement about what a manufacturer said is sourcing, not a self-award. A price quote
    carrying a real runtime-attached source must pass through byte-identical -- this guard runs on
    every answer, so a false positive here would mangle the lane that already works."""
    out, downgraded = downgrade_unearned_verification_labels(text)
    assert out == text, f"rewrote ordinary prose: {out!r}"
    assert downgraded == []


def test_a_price_answer_keeps_its_runtime_attached_source() -> None:
    quote = ("| Asset | Price | Source |\n| --- | --- | --- |\n"
             "| Gold | $4,255.30 USD | [Yahoo Finance](https://finance.yahoo.com/quote/GC=F) |")
    out, downgraded = downgrade_unearned_verification_labels(quote)
    assert out == quote and downgraded == []


# --------------------------------------------------------------------------------------------
# Raw tool-call JSON must never be shown as an answer
# --------------------------------------------------------------------------------------------

def test_a_tool_envelope_is_detected_as_leaked_syntax() -> None:
    """Measured 2026-08-05 on the aviation prompt: the model asked to run a search and the runtime
    rendered the REQUEST as the answer, with zero tool events in the trace.

    `foreign_markers` already detected this envelope and was wired only into the tool loop's
    synthesis check, never into the chat answer path -- so raw JSON reached the user there
    unchallenged."""
    from core.model_output_guard import foreign_markers

    leaked = '{"tool":"search","queries":["most produced commercial aircraft of all time"]}'
    assert foreign_markers(leaked), "the guard that exists must recognise this envelope"
    assert not foreign_markers("Boeing built more than 11,000 737s according to Boeing.")

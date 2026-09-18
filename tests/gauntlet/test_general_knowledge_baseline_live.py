"""Gauntlet_live — the frozen general-knowledge baseline, asserted on what the USER sees.

Tagged `known-good/general-knowledge-baseline` at `c624ac5d`, 2026-08-05.

These drive the real daemon over the real `/api/chat` surface, because the 10,002-test unit suite
was green through every one of the user-visible failures this file exists to catch: an ordinary
question ending in a canned fallback, a decorator deleting valid sentences, and a completed tool
loop returning no answer. A suite that tests components and not the product will stay green while
the product is broken -- so every assertion here is against the final text a person reads.

**The baseline is NOT "general knowledge works".** Measured on the same daemon, minutes apart:

    "most sold vw model, what engine, what colour, which country, what year"
      -> nemotron-3-ultra-550b-a55b:free -> full prose, sources cited, and it states which
         granular data is unpublished instead of inventing an engine or a colour.

    "most populare mercedez benz models? their engine sizes and rims sizes? ... where sold?"
      -> qwen3:8b (local) -> "I couldn't produce a normal chat response for that request."

Nearly identical question shapes, opposite outcomes, and the variable is the lane. So the frozen
state is *cloud answers general knowledge, local does not*, and the local case is recorded here as
a known failure rather than smoothed over. If it starts passing, `xfail` reports XPASS and someone
finds out -- which is the point of writing it down instead of leaving it in a doc.

Runs only with VOOL_LIVE_BASELINE=1 and a reachable daemon; the default lane blocks outbound
network, so these skip there by design rather than by accident.

    VOOL_LIVE_BASELINE=1 py -m pytest tests/gauntlet/test_general_knowledge_baseline_live.py -q
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.gauntlet_live

DAEMON = os.environ.get("VOOL_LIVE_BASELINE_URL", "http://127.0.0.1:11435")

# The exact strings a person typed, typos intact. A cleaned-up paraphrase would test a prompt the
# runtime has never actually failed on.
VW_PROMPT = (
    "what is the most sold vw model, what engine and what colour? "
    "also in what country it was sold the most and what year?"
)
MERCEDES_PROMPT = (
    "what are the most populare mercedez benz models? their engine sizes and rims sizes? "
    "ass well where most of those cars were or is sold?"
)
BMW_PROMPT = (
    "what is the average price of 2020 5th series bmw? "
    "What tires usually it comes with and what are the sizes of the engines?"
)

# Every generic runtime fallback that has stood in for an answer during this work. A reply that is
# one of these is a failure however non-empty it looks.
RUNTIME_FALLBACKS = (
    "i couldn't produce a normal chat response",
    "i couldn't get a usable model response",
    "did not return a usable reply",
    "i couldn't map that cleanly to a real action",
    "i couldn't resolve that cleanly",
    "model synthesis failed",
    "empty_synthesis",
)

# Text that belongs to the runtime's own plumbing and must never reach a person as their answer.
LEAKED_INTERNALS = (
    '"intent"',
    '"tool"',
    "traceback (most recent call last)",
    "httpconnectionpool",
    "structured_output",
    "tool_choice",
)


def _live() -> None:
    if os.environ.get("VOOL_LIVE_BASELINE") != "1":
        pytest.skip("set VOOL_LIVE_BASELINE=1 to drive the real daemon")
    try:
        urllib.request.urlopen(f"{DAEMON}/api/tags", timeout=5).read(1)
    except Exception as exc:
        pytest.skip(f"daemon unreachable at {DAEMON}: {type(exc).__name__}")


def ask(prompt: str, *, timeout: float = 600.0) -> str:
    """The final text a person reads, through the same endpoint the app uses."""
    body = json.dumps(
        {"model": "vool", "stream": False, "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    request = urllib.request.Request(
        f"{DAEMON}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return str((payload.get("message") or {}).get("content") or "")


def assert_is_a_real_answer(answer: str, *, must_mention: tuple[str, ...] = ()) -> None:
    """The shared contract: a reply a person can use, not a runtime status dressed as one."""
    assert answer.strip(), "empty final answer"
    lowered = answer.lower()
    for fallback in RUNTIME_FALLBACKS:
        assert fallback not in lowered, f"answer is the runtime fallback: {fallback!r}"
    for leak in LEAKED_INTERNALS:
        assert leak not in lowered, f"internal runtime text leaked to the user: {leak!r}"
    missing = [term for term in must_mention if term.lower() not in lowered]
    assert not missing, f"the answer never addresses: {missing}"


# --------------------------------------------------------------------------------------------
# Frozen as working
# --------------------------------------------------------------------------------------------

def test_a_five_part_general_knowledge_question_is_answered_in_one_turn() -> None:
    """The VW drive, verbatim. Five parts, one turn, one answer.

    Before the servable-split gate this shape was cut into five subject-less fragments; before the
    planner it answered one part and dropped four. Both regressions land here.
    """
    _live()
    answer = ask(VW_PROMPT)
    assert_is_a_real_answer(answer, must_mention=("volkswagen",))
    assert len(answer.split()) > 25, "a five-part question cannot be answered in a sentence"


def test_the_answer_does_not_invent_the_fields_the_sources_lack() -> None:
    """The measured answer named the Golf and Tiguan and declined to state an engine or a colour,
    because no aggregated sales report publishes that split.

    Asserted on the ABSENCE of a fabricated specification, not on the presence of a hedging phrase.
    The first version of this test matched a list of phrasings ("not published", "couldn't find")
    and failed against a perfectly good answer that said "No specific engine or color details are
    mentioned in the sources" -- that is testing the model's wording, which Section 6 rules out. A
    displacement or a colour name here IS the defect; how the model explains its absence is not the
    runtime's business.
    """
    _live()
    answer = ask(VW_PROMPT).lower()
    invented_engine = re.search(r"\b\d\.\d\s*(?:l|litre|liter)\b|\btsi\b|\btdi\b", answer)
    invented_colour = re.search(
        r"\b(?:most (?:common|popular) colou?r (?:is|was)|colou?r:\s*)\s*(?:white|black|silver|grey|gray|blue|red)\b",
        answer,
    )
    assert not invented_colour, f"invented a best-selling colour: {invented_colour.group(0)!r}"
    if invented_engine:
        # An engine may legitimately appear as an example the user asked to explore, but not as an
        # answer to "what engine" when no source carries that split.
        assert re.search(r"not (?:mention|specif|publish|availab|broken)", answer), (
            f"stated an engine ({invented_engine.group(0)!r}) with no note that sources lack it"
        )


@pytest.mark.parametrize(
    "prompt, must_mention",
    [
        ("hi, what is the gold, silver and BNB price now?", ("gold", "silver")),
        ("what is the price of ARB and LTC ?", ("arbitrum", "litecoin")),
        ("what is weather in Vilnius and Riga now?", ("vilnius", "riga")),
    ],
)
def test_a_multi_asset_lookup_still_answers_every_part(prompt, must_mention) -> None:
    """The lookups the split exists for. These must not regress while answer quality is worked on."""
    _live()
    assert_is_a_real_answer(ask(prompt), must_mention=must_mention)


def test_a_single_request_keeps_its_deterministic_path() -> None:
    """The common case must not pay for any of this."""
    _live()
    assert_is_a_real_answer(ask("what is the price of bitcoin right now?"), must_mention=("bitcoin",))


# --------------------------------------------------------------------------------------------
# Frozen as FAILING — recorded, not hidden
# --------------------------------------------------------------------------------------------

@pytest.mark.xfail(
    reason=(
        "Local lane (qwen3:8b) returns the runtime fallback for a heavy general-knowledge prompt "
        "while the free cloud lane answers the same shape. Measured 2026-08-05. Not yet "
        "root-caused: the raw provider envelope has never been captured, so it is unproven whether "
        "the model returned nothing or the runtime discarded a real answer. XPASS here means it "
        "started working and the baseline needs re-freezing."
    ),
    strict=False,
)
@pytest.mark.parametrize("prompt", [MERCEDES_PROMPT, BMW_PROMPT])
def test_a_heavy_general_knowledge_question_is_answered(prompt) -> None:
    _live()
    assert_is_a_real_answer(ask(prompt))


@pytest.mark.xfail(
    reason=(
        "Cross-lane answers come from separate sub-turns, so merge_outcomes holds rendered strings "
        "and has no structured quotes to table. Needs run_one to carry structured results out. "
        "Single-lane multi-quote DOES table -- covered above by the ARB/LTC case."
    ),
    strict=False,
)
def test_a_cross_lane_multi_asset_answer_is_a_table() -> None:
    _live()
    answer = ask("hi, what is the gold, silver and BNB price now?")
    assert "| Asset |" in answer, "three assets across two lanes should read as a comparison table"


@pytest.mark.xfail(
    reason=(
        "_decorate_chat_response deletes the price and source sentences and keeps only the change. "
        "Bisected to that call; the step inside it is unknown. Reproduces on a fresh daemon as the "
        "first query, so it is not session state."
    ),
    strict=False,
)
def test_a_quote_answer_survives_decoration_intact() -> None:
    """The exact HBAR text, frozen as a fixture per the review directive.

    In:  "Hedera Hashgraph is $0.0699 USD as of ... 24h change: -1.41%. Source: [CoinGecko](...)."
    Out: "24h change: -1.41%."
    """
    _live()
    answer = ask("what is the price of HBAR right now?")
    assert "hedera" in answer.lower(), "the asset name was deleted from its own quote"
    assert "$" in answer, "the price was deleted from its own quote"
    assert "source" in answer.lower(), "the source was deleted from its own quote"


# --------------------------------------------------------------------------------------------
# The Corolla fixture — the missing truthfulness boundary, frozen as failing
# --------------------------------------------------------------------------------------------

COROLLA_PROMPT = (
    "what is the most sold car model ever, corolla or golf? what engine and colour was most common, "
    "in which country did it sell most and in what year? separate verified facts from unavailable "
    "data, and do not guess"
)

# Labels a model must never award itself. Only the runtime may attach these, and only to a claim
# carrying evidence -- see the ledger, "tiered truthfulness". Protection (A), the cheapest of the
# four and the one that alone would have stopped this table reading as sourced.
_SELF_AWARDED_VERIFICATION = (
    "verified fact", "confirmed fact", "proven", "officially confirmed", "fact-checked",
)


@pytest.mark.xfail(
    reason=(
        "No truthfulness boundary exists yet -- this is a MISSING layer, not a regression; nothing "
        "broke. Measured 2026-08-05: the runtime answered this in one turn with a 'Verified fact' "
        "column and filled every cell, including four with no source. One is internally "
        "contradictory ('1.6L 1ZR-FAE / 2ZR-FAE' names a 1.8L/1797cc engine) and one appears to "
        "mangle Toyota's 22.65M cumulative and 1.22M 2013-annual figures into an invented 1997 "
        "peak. XPASS means protection (A) landed."
    ),
    strict=False,
)
def test_a_model_may_not_award_itself_a_verified_label() -> None:
    """The evaluator must FAIL this response even though it is fluent, structured and non-empty.

    That is the whole acceptance criterion. A polished table does not make a fabrication better --
    the fluency is what makes it credible, so an evaluator that rewards structure and non-emptiness
    scores this HIGHER than the cautious VW answer that correctly declined the same fields.

    Asserted on the LABEL, not on the facts. Checking whether the engine or the colour is right
    would need authoritative data this test does not have, and a second model's recall is not
    verification -- it is model-versus-model. Whether the runtime let the model call its own guess
    "verified" is decidable from the text alone.
    """
    _live()
    answer = ask(COROLLA_PROMPT).lower()
    awarded = [label for label in _SELF_AWARDED_VERIFICATION if label in answer]
    assert not awarded, (
        f"the model awarded itself {awarded} -- only the runtime may attach a verification label, "
        f"and only to a claim carrying evidence"
    )


@pytest.mark.xfail(
    reason="Grounded mode does not exist yet; see the ledger for protections (B) and (C).",
    strict=False,
)
def test_a_do_not_guess_request_marks_unavailable_fields_unavailable() -> None:
    """"do not guess" is a contract model weights alone cannot satisfy.

    The measured answer supplied a most-common engine, a most-common colour, a top country with
    invented precision (12-13M) and a best year -- none of which any cited source carries. The
    correct output for those cells is an explicit unavailable marker.
    """
    _live()
    answer = ask(COROLLA_PROMPT).lower()
    unavailable = ("no reliable", "not available", "unavailable", "no authoritative",
                   "no global breakdown", "not published")
    assert any(marker in answer for marker in unavailable), (
        "every requested field was filled despite 'do not guess'; unsupported cells must render "
        "an unavailable marker rather than a plausible completion"
    )

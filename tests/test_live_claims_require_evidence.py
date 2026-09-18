"""A chat-lane reply may not state current readings the turn never observed.

Measured live 2026-08-15 (operator transcript, 12:45). The prior turn reported weather UNAVAILABLE
-- both lookups failed. The user said "not answered in full wtf?!", the turn was classified
`unknown` and routed to the ordinary chat lane, and nemotron -- with ZERO tools run that turn --
answered with full confidence:

    Warsaw: 22 C, partly cloudy; Manchester: 18 C
    flights -- Luton: EUR 42; Gatwick: EUR 58
    last 7 days: BTC +4.8%, BNB +6.2%

The exact values the runtime had just failed to obtain, invented, wearing a correct answer's
shape. Reproduced at HEAD aa1c3038 before the fix: `_validate_final_chat_output` shipped that
text byte-for-byte with `final_ui.fallback_applied == False`.

The invariant is a conjunction, both halves structural:

  1. the reply binds a QUANTIFIED live value (currency symbol/code + number, a temperature, a
     signed/directional percent change) to a CURRENTNESS anchor (now / currently / today /
     tonight / this week / last N days) inside one +/-120-character window, unhedged
     (typically/usually/on average exempts) and unattributed (Source:/URL/observed-time claims
     stay with the guards that own observed and requires-current turns);
  2. the turn ran NO observations -- every same-turn evidence channel on `source_context` is
     empty (`runtime_tool_observations`, retrieval receipts, user-supplied material).

Fire both and the reply is replaced with a notice naming what it declined to invent. Miss either
and the reply passes untouched -- history, arithmetic, code, hedged climate prose, grounded
turns, and raw-contract turns are all somebody else's jurisdiction or nobody's.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.response import _validate_final_chat_output
from core.model_output_guard import (
    turn_ran_observations,
    unobserved_live_value_claims,
    unverified_live_value_notice,
)

# The measured fabrication, as one reply, exactly the three value families the transcript shows.
FABRICATED_REPLY = (
    "Here is the full answer. Current weather - Warsaw: 22 C, partly cloudy; Manchester: 18 C. "
    "Cheapest flights today: Luton: EUR 42; Gatwick: EUR 58. "
    "Crypto over the last 7 days: BTC +4.8%, BNB +6.2%."
)


# ---------------------------------------------------------------------------------------------
# The measured failure, at the shipped seam
# ---------------------------------------------------------------------------------------------


def test_the_measured_fabricated_reply_is_replaced_at_the_seam() -> None:
    source_context: dict[str, object] = {}
    committed = _validate_final_chat_output(FABRICATED_REPLY, source_context=source_context)

    assert committed != FABRICATED_REPLY
    # Not one invented reading survives.
    for invented in ("22", "18", "42", "58", "4.8", "6.2"):
        assert invented not in committed, invented
    # The notice names what it declined to invent.
    for kind in ("temperature", "price", "percent-change"):
        assert kind in committed, kind
    final_ui = dict(source_context["response_control"]["final_ui"])  # type: ignore[index]
    assert final_ui["unobserved_live_claims_rejected"] == [
        "temperature",
        "price",
        "percent-change",
    ]
    assert final_ui["fallback_applied"] is True


def test_the_detector_sees_all_three_measured_value_families() -> None:
    assert unobserved_live_value_claims(FABRICATED_REPLY) == (
        "temperature",
        "price",
        "percent-change",
    )


# ---------------------------------------------------------------------------------------------
# Anti-overfit family: unseen phrasings, formats and domains, same defect shape
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    (
        # Degree sign and a different city; anchor after the value.
        "It's a warm 31°C in Madrid right now, perfect beach weather.",
        # Unicode temperature glyph.
        "Tokyo sits at 29℃ today with high humidity.",
        # Fahrenheit, spelled anchor.
        "Phoenix is currently 104 F, so stay indoors.",
        # Symbol-first price, ticketing domain the transcript never mentioned.
        "Front-row seats are going for $89 tonight at the arena.",
        # Code-after-number currency order.
        "The gadget costs 1,299 EUR this week at the official store.",
        # Pound symbol, hotel domain.
        "A double room runs £120 tonight near the station.",
        # Directional percent change without a sign character.
        "Tesla stock is up 6.2% today after the earnings call.",
        # Signed negative move, commodities.
        "Brent crude moved -2.1% over the past 3 days.",
    ),
)
def test_unseen_live_claim_shapes_are_caught(reply: str) -> None:
    assert unobserved_live_value_claims(reply), reply
    assert _validate_final_chat_output(reply, source_context={}) != reply, reply


# ---------------------------------------------------------------------------------------------
# Negative controls: every one must ship byte-for-byte
# ---------------------------------------------------------------------------------------------

NEGATIVE_CONTROLS: tuple[tuple[str, dict[str, object]], ...] = (
    # 1. A historical fact -- numbers, no live-value binding.
    ("The Toyota Corolla has sold over 15 million units since 1966.", {}),
    # A historical fact that DOES carry a value shape but no currentness anchor. This is the
    # control the anchor requirement exists for: widen the detector by dropping the anchor and
    # this ships a rejection notice for a legitimate answer.
    ("The record high in Warsaw was 37 C, set back in 1994.", {}),
    ("In 1995 a cinema ticket cost $4.35 on average across the US.", {}),
    # 2. A math answer.
    ("31 + 51 = 82.", {}),
    ("A 20% tip on $45 is $9, so you'd pay $54 in total.", {}),
    # 3. Code containing numbers, currency strings and even an anchor word in a comment.
    (
        "Here's the conversion helper:\n"
        "```python\n"
        "RATE_NOW = 1.09  # EUR 1 = $1.09 today\n"
        "def to_usd(eur: float) -> float:\n"
        "    return eur * RATE_NOW\n"
        "```",
        {},
    ),
    # 4. A hedge turns a reading into climate knowledge.
    ("Warsaw is typically 24-27 C in August, so pack light layers.", {}),
    ("Flights to Luton usually run 40-60 euros this time of year.", {}),
    # 5. An attributed reading ON A TURN THAT REALLY OBSERVED -- the live lane's own composed shape.
    #
    # This control used to be spelled with an EMPTY context, on the reasoning that the live lane's
    # receipts "do not travel on every context dict that reaches the final seam" and that a
    # fabricated attribution would be convicted instead by the requires-current lane. Both halves
    # were measured at the real `/api/chat` seam at eca76ff9 and both are wrong: a successful
    # live-data turn reaches the seam WITH its receipts (`turn_ran_observations=True`,
    # `web_retrieval_receipts=1`), so this branch is never entered on the path being protected --
    # while a turn that observed nothing shipped `Bergen: Cloudy, 41.7 C right now. Source:
    # wttr.in, observed 2026-08-07T12:00:00Z.` untouched, because naming a source exempted it. The
    # same sentence WITHOUT the source line was convicted, so the runtime was safer when a model
    # invented a reading and named nobody.
    #
    # The control keeps its job -- prove a real grounded answer ships byte for byte -- and now
    # states the condition that actually makes it grounded, instead of asserting that an
    # attribution is self-justifying. `test_an_attributed_reading_is_not_evidence_of_its_own_source`
    # below is the other half.
    (
        "Berlin: Sunny, 27 C (today's high 28 C / low 16 C). Source: wttr.in, observed 05:48 PM.",
        {"web_retrieval_receipts": [{"status": "available", "source_count": 1, "failure_class": ""}]},
    ),
)


@pytest.mark.parametrize("reply, context", NEGATIVE_CONTROLS)
def test_negative_controls_ship_byte_for_byte(reply: str, context: dict[str, object]) -> None:
    source_context = dict(context)
    assert _validate_final_chat_output(reply, source_context=source_context) == reply, reply


def test_a_turn_with_observations_keeps_its_live_answer() -> None:
    """The grounded lane is exempt: the guards that own observed turns keep jurisdiction."""

    reply = "Warsaw is 22 C right now with light cloud cover."
    source_context: dict[str, object] = {
        "runtime_tool_observations": [
            {"intent": "web.weather", "ok": True, "response_preview": "Warsaw 22 C cloudy"}
        ]
    }
    assert turn_ran_observations(source_context) is True
    assert _validate_final_chat_output(reply, source_context=source_context) == reply


def test_a_raw_contract_turn_is_exempt_because_contract_handling_wins() -> None:
    reply = "It is 22 C right now in Warsaw."
    source_context: dict[str, object] = {"raw_output_contract": {"raw_only": True}}
    committed = _validate_final_chat_output(reply, source_context=source_context)
    assert "invented" not in committed
    assert "22" in committed


def test_a_failed_observation_is_not_an_observation() -> None:
    """A failed lookup proves an attempt. It does not prove the turn saw anything.

    This test asserted the OPPOSITE until eca76ff9 -- "a turn that RAN a lookup, even a failed one,
    belongs to the synthesis guards, not to this seam" -- and the premise was measured false at the
    real `/api/chat` seam: on the reproduced turn `core.unsourced_current_claim` read the SAME failed
    receipt, reported `has_evidence=True`, and declined to convict too. No synthesis guard owned it,
    so `Porto: Cloudy, high 22 C, low 17 C right now. Source: wttr.in, observed <the previous turn's
    timestamp>` shipped. The exempting direction is only safe as far as SUCCESS.
    """

    assert turn_ran_observations(
        {"web_retrieval_receipts": [{"status": "failed", "source_count": 0}]}
    ) is False
    assert turn_ran_observations(
        {"web_retrieval_receipts": [{"status": "available", "source_count": 1}]}
    ) is True
    assert turn_ran_observations({}) is False
    assert turn_ran_observations(None) is False


def test_an_attributed_reading_is_not_evidence_of_its_own_source() -> None:
    """Naming a source is a claim about provenance, not evidence of it.

    Measured at the real seam: with no observation on the turn, the attributed sentence shipped and
    the identical unattributed sentence was convicted -- so a fabricated `Source:` line was worth
    more to a fabricated reading than saying nothing. Both are convicted now, and a turn that really
    observed keeps its answer (the negative control in `NEGATIVE_CONTROLS` #5, and
    `test_a_turn_with_observations_keeps_its_live_answer`).
    """

    attributed = "Bergen: Cloudy, 41.7 C right now. Source: wttr.in, observed 2026-08-07T12:00:00Z."
    bare = "Bergen: Cloudy, 41.7 C right now."
    assert unobserved_live_value_claims(attributed) == ("temperature",)
    assert unobserved_live_value_claims(bare) == ("temperature",)

    committed = _validate_final_chat_output(attributed, source_context={})
    assert "41.7" not in committed
    assert "wttr.in" not in committed
    assert "invented" in committed


# ---------------------------------------------------------------------------------------------
# The notice
# ---------------------------------------------------------------------------------------------


def test_the_notice_names_the_declined_kinds_and_states_no_value() -> None:
    notice = unverified_live_value_notice(("temperature", "price"))
    assert "temperature" in notice
    assert "price" in notice
    assert not any(ch.isdigit() for ch in notice)
    # The notice itself must not re-trip the detector.
    assert unobserved_live_value_claims(notice) == ()


# ---------------------------------------------------------------------------------------------
# The wiring is load-bearing: disable either conjunct and the measured failure returns
# ---------------------------------------------------------------------------------------------


def test_sabotage_blinding_the_detector_recommits_the_fabrication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.agent_runtime.response as response_module

    monkeypatch.setattr(response_module, "unobserved_live_value_claims", lambda _text: ())
    assert _validate_final_chat_output(FABRICATED_REPLY, source_context={}) == FABRICATED_REPLY


def test_sabotage_faking_observations_recommits_the_fabrication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.agent_runtime.response as response_module

    monkeypatch.setattr(response_module, "turn_ran_observations", lambda _ctx: True)
    assert _validate_final_chat_output(FABRICATED_REPLY, source_context={}) == FABRICATED_REPLY

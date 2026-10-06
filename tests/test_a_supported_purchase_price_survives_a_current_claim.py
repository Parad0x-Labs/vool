"""A supported historical value survives beside a withdrawn current claim.

Until this repair, the final seam replaced the WHOLE answer when any sentence
bound a value to a currentness anchor with nothing observed: "You paid about
$170 when you bought them; today they are listed at $120." was destroyed by
its second half. The saved LME case q254a2bf -- a historical worth question
delivered as a live-price refusal -- is this shape.

The claim lives in the sentence that binds the value to its anchor, so the
withdrawal is now sentence-granular. The whole-text trigger is unchanged: a
reply whose every sentence is clean still goes wholesale, so the guard is
never weaker than before.

FRESH ACCEPTANCE rows were frozen before first execution, with different
subjects, values and domains from every saved example.
"""

from core.model_output_guard import (
    replace_unobserved_live_claims,
    unobserved_live_value_claims,
    unverified_live_value_notice,
)

_NOTICE_LEAD = unverified_live_value_notice(("price",)).split(",")[0]


# ---------------------------------------------------------------- regression


def test_mixed_answer_keeps_its_supported_past_half() -> None:
    delivered = replace_unobserved_live_claims(
        "You paid about $170 when you bought them. Today they are listed at $120."
    )
    assert "about $170 when you bought them" in delivered
    assert "$120" not in delivered
    assert _NOTICE_LEAD in delivered


def test_wholly_current_answer_loses_every_value() -> None:
    answer = "The headphones cost $170 right now. Trust me."
    delivered = replace_unobserved_live_claims(answer)
    assert _NOTICE_LEAD in delivered
    assert "$170" not in delivered
    # The value-free tail sentence is not a claim and survives the split.


def test_clean_answer_ships_unchanged() -> None:
    answer = "You paid about $170 when you bought them."
    assert replace_unobserved_live_claims(answer) == answer


def test_single_conviicted_sentence_becomes_the_notice() -> None:
    # One sentence, no separable clean half: today's whole replacement.
    delivered = replace_unobserved_live_claims("The current price is $64,102.")
    assert delivered == unverified_live_value_notice(("price",))


# ------------------------------------------------------- no-weakening controls


def test_cross_sentence_window_still_convicts_wholesale() -> None:
    # The anchor sits in the neighbouring sentence; no sentence self-convicts,
    # so nothing is separable and the whole answer goes -- exactly as before.
    answer = "Right now the market is moving. The BTC price stands at $64,102."
    assert unobserved_live_value_claims(answer) == ("price",)
    delivered = replace_unobserved_live_claims(answer)
    assert delivered.startswith(_NOTICE_LEAD)
    assert "$64,102" not in delivered


def test_hedged_climate_prose_still_passes() -> None:
    answer = "The Baltic is typically around 15-18 C in late summer."
    assert replace_unobserved_live_claims(answer) == answer


# ---------------------------------------------------------- fresh acceptance


def test_fresh_mixed_tenant_answer_keeps_rent_and_drops_market_rate() -> None:
    delivered = replace_unobserved_live_claims(
        "Your rent was fixed at EUR 620 when you signed the contract in Kaunas. "
        "The building's units currently fetch around EUR 900 per month."
    )
    assert "EUR 620" in delivered
    assert "EUR 900" not in delivered
    assert _NOTICE_LEAD in delivered


def test_fresh_three_sentence_answer_keeps_both_supported_halves() -> None:
    delivered = replace_unobserved_live_claims(
        "The deposit came to 2,400 euros at signing. The agency fee was 350 euros. "
        "Similar flats are listed at 1,100 euros today."
    )
    assert "2,400 euros" in delivered
    assert "350 euros" in delivered
    assert "1,100 euros" not in delivered
    assert _NOTICE_LEAD in delivered


def test_fresh_current_temperature_pair_is_fully_withdrawn() -> None:
    delivered = replace_unobserved_live_claims(
        "Nida is 21 C right now. Palanga is 19 C right now."
    )
    assert delivered.startswith(_NOTICE_LEAD)
    assert "21 C" not in delivered
    assert "19 C" not in delivered


def test_fresh_percent_change_sentence_does_not_kill_static_neighbour() -> None:
    delivered = replace_unobserved_live_claims(
        "You bought 40 shares at USD 12 each in March. The position is up 6.2% this week."
    )
    assert "USD 12" in delivered
    assert "6.2%" not in delivered
    assert _NOTICE_LEAD in delivered

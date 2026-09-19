"""CP2 reproductions — confirmed mis-binds LIVE at audited HEAD 091ed83b. RED by design.

Each test asserts the CORRECT behaviour and fails today; the finding id maps to
validation-logs/conversation-truth/CP6_REVIEW_HANDOFF.md. Owning seam for all five
families: core/live_data_continuation.py (RE-ASK residue gate :800-807 + _SCAFFOLDING
:128-145; REBIND _slot_filler_for :534-589; AGGREGATE :836-855; clarification word
accounting :723-751).
"""
from __future__ import annotations

from kit_lib import GOLD_ANSWER, GOLD_QUESTION, context, gold_thread, plan_operations, weather_thread

from core.live_data_continuation import continuation_inherits_live_data

GOLD = GOLD_QUESTION
GOLD_ANS = GOLD_ANSWER


def _gold_ctx(label: str, text: str) -> dict:
    session = gold_thread(label)
    return context(session, ("user", GOLD), ("assistant", GOLD_ANS), ("user", text))


def _weather_ctx(label: str, slots: list[str], request: str, answer: str, text: str) -> dict:
    session = weather_thread(label, slots, request)
    return context(session, ("user", request), ("assistant", answer), ("user", text))


# ------------------------------------------------------------ CT-201: scaffolding domain nouns

def test_CT201_a_cross_domain_nudge_is_not_a_bare_nudge():
    """'and the weather there?' after a gold turn asks WEATHER; 'and the price?' after a
    weather turn asks a PRICE. _SCAFFOLDING holds both nouns (weather/price/temperature/
    news...), so the residue strips them and the turn is treated as a bare nudge that
    re-asks the OLD obligation. Correct: the cross-domain noun makes it a new (if
    underspecified) request — decline, let the model lane ask."""
    for text in ["and the weather there?", "and the temperature?"]:
        source = _gold_ctx("r201a", text)
        inherited = continuation_inherits_live_data(text, source_context=source)
        assert inherited == "", (text, inherited)
        assert plan_operations(text, source) == [], text

    source = _weather_ctx("r201b", ["Kaunas"], "Get weather for Kaunas.", "Kaunas: 18 C.", "and the price?")
    inherited = continuation_inherits_live_data("and the price?", source_context=source)
    assert inherited == "", inherited
    assert plan_operations("and the price?", source) == []

    source = _weather_ctx("r201c", ["Kaunas"], "Get weather for Kaunas.", "Kaunas: 18 C.", "what about the news?")
    assert continuation_inherits_live_data("what about the news?", source_context=source) == ""


# ------------------------------------------------------------ CT-202: correction inversion

def test_CT202_a_negated_subject_never_wins_the_rebind():
    """'not gold, silver' — the user corrects FROM gold TO silver. Mechanism (adversarial
    review corrected): `_residue_span` strips the leading 'not' (CORRECTION_OPENING_RE,
    :527) AND `_slot_filler_for` takes the FIRST alias by position (:577-579); the existing
    market-negation guard could not have caught it even without the strip, because it keys
    on market TERMS (price/quote/value vocabulary, semantic_claim_authority.py:344-353),
    not on asset names — 'gold' is not a market term. The answer replaces a correction
    with the exact thing corrected away. Correct: the named target silver is served (as
    'no, i meant silver' already is) — never the cue-negated alias gold."""
    source = _gold_ctx("r202", "not gold, silver")
    inherited = continuation_inherits_live_data("not gold, silver", source_context=source)
    assert "gold" not in inherited.lower(), inherited
    ops = plan_operations("not gold, silver", source)
    assert ("market_quote", "Gold") not in ops, ops
    assert ("market_quote", "Silver") in ops, ops


def test_CT202_the_negation_guard_does_not_rank_a_cue_negated_asset_first():
    """The authorization layer itself: even on the UNSTRIPPED text, first-alias-by-position
    returns the negated asset — the guard that exists keys on market terms, not asset
    names. A repair must scope negation per asset mention, not reorder guards."""
    from core.semantic_claim_authority import price_assets_named

    ranked = price_assets_named("not gold, silver", domain_already_authorized=True)
    assert "gold" not in ranked[:1], ranked


# ------------------------------------------------------------ CT-207: quoted-prior entity leak

def test_CT207_a_quoted_prior_question_contributes_no_entities_to_the_new_ask():
    """Quoting the old question while asking a new one does not rebind (control t4), but
    the entity enumeration still harvests the QUOTED subject: the plan serves the old
    subject alongside the new one (and a named-but-unsupported member like platinum is
    silently absent). Correct: a quoted span is talk about the conversation, not a subject
    of the new request."""
    text = 'you asked "what is gold price now?" - now do the same for silver vs platinum'
    source = _gold_ctx("r207", text)
    ops = plan_operations(text, source)
    assert ("market_quote", "Gold") not in ops, ops


# ------------------------------------------------------------ CT-203: alias-mention rebind

def test_CT203_a_general_question_about_the_asset_is_not_a_price_reask():
    """Any gold-alias mention inside a <=3-word residue rebinds the price obligation with
    domain_already_authorized=True: general-knowledge and creative requests about the
    asset become quote re-asks. Correct: only a price-demand phrasing rebinds."""
    for text in [
        "how much gold is there?",
        "tell me the history of gold",
        "how is gold mined?",
        "write a poem about gold",
    ]:
        source = _gold_ctx("r203", text)
        inherited = continuation_inherits_live_data(text, source_context=source)
        assert inherited == "", (text, inherited)
        assert plan_operations(text, source) == [], text


# ------------------------------------------------------------ CT-204: Title-case weather rebind

def test_CT204_a_capitalised_creative_request_is_not_a_weather_place():
    """The weather rebind accepts any Title-cased <=3-word residue as a location
    (_slot_filler_for :584-586 + _is_plausible_weather_location passing 'Write Winter' /
    'Code Python'): phone auto-capitalisation turns a writing/code request into a weather
    lookup for a non-place. Correct: these are not weather requests — decline."""
    for text in ["Write About Winter", "Code About Python Now"]:
        source = _weather_ctx("r204", ["Kaunas"], "Get weather for Kaunas.", "Kaunas: 18 C.", text)
        inherited = continuation_inherits_live_data(text, source_context=source)
        assert inherited == "", (text, inherited)
        assert plan_operations(text, source) == [], text


# ------------------------------------------------------------ CT-205: aggregate shape overbreadth

def test_CT205_a_non_comparison_er_word_is_not_a_set_comparison():
    """The aggregate gate counts any >=5-letter word ending er/est as 'the comparison'
    (:855): 'river', 'water', 'winter' qualify, so a geography question about the set's
    members becomes a weather re-ask. Correct: only comparison-shaped aggregates re-ask."""
    for text in ["which one has a river?", "which one has water?"]:
        source = _weather_ctx("r205", ["Kaunas", "Tallinn"], "Compare weather in Kaunas and Tallinn.",
                              "Kaunas 18 C, Tallinn 12 C.", text)
        inherited = continuation_inherits_live_data(text, source_context=source)
        assert inherited == "", (text, inherited)
        assert plan_operations(text, source) == [], text


# ------------------------------------------------------------ CT-206: near-slot clarification

def test_CT206_a_near_slot_mention_without_restatement_is_not_a_clarification():
    """'and golf, them two, was that what i asked about?' — a new near-slot subject plus
    set grammar still rebinds gold (the incident's residue in a different coat: the slot
    is not restated verbatim, so the typo-only requirement is satisfied by 'golf' itself).
    Correct: a turn introducing a near-slot newcomer it does not restate is a new
    question — decline."""
    text = "and golf, them two, was that what i asked about?"
    source = _gold_ctx("r206", text)
    inherited = continuation_inherits_live_data(text, source_context=source)
    assert inherited == "", inherited
    assert plan_operations(text, source) == [], text

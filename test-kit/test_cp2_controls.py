"""CP2 controls — conversation-truth laws that HOLD at audited HEAD 091ed83b.

Green at HEAD. Every red companion lives in repro_ct*.py with a finding id.
"""
from __future__ import annotations

from core.live_data_continuation import continuation_inherits_live_data

from kit_lib import GOLD_ANSWER, GOLD_QUESTION, context, gold_thread, plan_operations, weather_thread

GOLD = GOLD_QUESTION
GOLD_ANS = GOLD_ANSWER


def _gold_ctx(label: str, text: str) -> dict:
    session = gold_thread(label)
    return context(session, ("user", GOLD), ("assistant", GOLD_ANS), ("user", text))


# ---------------------------------------------------------------- genuine follow-ups

def test_bare_nudge_still_re_asks_the_live_question():
    assert continuation_inherits_live_data("and now?", source_context=_gold_ctx("n1", "and now?")) == GOLD


def test_follow_up_naming_a_new_asset_rebinds_to_it():
    source = _gold_ctx("n2", "what about silver?")
    assert continuation_inherits_live_data("what about silver?", source_context=source) == "what is silver price now?"
    assert plan_operations("what about silver?", source) == [("market_quote", "Silver")]


def test_clean_correction_rebinds_to_the_corrected_asset():
    """'no, i meant silver' — the correction names its target; the rebind follows the target."""
    source = _gold_ctx("n3", "no, i meant silver")
    assert plan_operations("no, i meant silver", source) == [("market_quote", "Silver")]


def test_explicit_comparison_is_its_own_request():
    source = _gold_ctx("n4", "compare gold vs silver price")
    assert continuation_inherits_live_data("compare gold vs silver price", source_context=source) == ""
    assert plan_operations("compare gold vs silver price", source) == [("market_quote", "Gold"), ("market_quote", "Silver")]


def test_typo_clarification_still_recovers_the_whole_set():
    session = weather_thread("n5", ["Kaunas", "Tallinn"], "Compare weather in Kaunas and Tallinn.")
    text = "i asked about kauans and talling weather, last question was follow up for them two right?"
    source = context(session, ("user", "Compare weather in Kaunas and Tallinn."),
                     ("assistant", "Kaunas 18 C, Tallinn 12 C."), ("user", text))
    assert plan_operations(text, source) == [("weather_lookup", "Kaunas"), ("weather_lookup", "Tallinn")]


def test_genuine_aggregate_comparison_still_re_asks_the_set():
    session = weather_thread("n6", ["Kaunas", "Tallinn"], "Compare weather in Kaunas and Tallinn.")
    for text in ["which one is warmer?", "which one is warmest?", "which of those is up more?"]:
        source = context(session, ("user", "Compare weather in Kaunas and Tallinn."),
                         ("assistant", "Kaunas 18 C, Tallinn 12 C."), ("user", text))
        assert plan_operations(text, source) == [("weather_lookup", "Kaunas"), ("weather_lookup", "Tallinn")], text


# ---------------------------------------------------------------- topic changes (must decline)

def test_unrelated_creative_request_after_gold_declines():
    source = _gold_ctx("t1", "help me write a short poem about the sea")
    assert continuation_inherits_live_data("help me write a short poem about the sea", source_context=source) == ""
    assert plan_operations("help me write a short poem about the sea", source) == []


def test_code_request_after_weather_declines():
    session = weather_thread("t2", ["Kaunas"], "Get weather for Kaunas.")
    text = "write me a python function that sorts a list"
    source = context(session, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", text))
    assert continuation_inherits_live_data(text, source_context=source) == ""
    assert plan_operations(text, source) == []


def test_explicit_return_to_an_older_topic_answers_the_named_topic():
    """An intervening weather turn does not trap the thread: naming gold again answers gold."""
    session = gold_thread("t3")
    text = "back to gold, what is the price?"
    source = context(session, ("user", GOLD), ("assistant", GOLD_ANS),
                     ("user", "what about the weather in Rome?"), ("assistant", "Rome: 25 C."), ("user", text))
    assert plan_operations(text, source) == [("market_quote", "Gold")]


def test_quoted_prior_question_does_not_inherit_the_old_request():
    """Quoting the old question while asking a new one must not rebind the old obligation.
    (The quoted-span ENTITY leak on this input is repro CT-207.)"""
    text = 'you asked "what is gold price now?" - now do the same for silver'
    source = _gold_ctx("t4", text)
    assert continuation_inherits_live_data(text, source_context=source) == ""


def test_ambiguous_reference_does_not_silently_resurrect_the_old_task():
    for text in ["what about that other thing?", "ok so what about the stuff?"]:
        source = _gold_ctx("t5", text)
        assert continuation_inherits_live_data(text, source_context=source) == "", text
        assert plan_operations(text, source) == [], text


def test_multilingual_nudges_never_substitute_the_old_answer():
    """Non-English nudges decline (fail-safe to the model lane) — they must never re-ask gold."""
    for text in ["а сейчас?", "y ahora?", "und jetzt?", "o dabar?"]:
        source = _gold_ctx("m1", text)
        inherited = continuation_inherits_live_data(text, source_context=source)
        assert inherited == "", (text, inherited)
        assert plan_operations(text, source) == [], text

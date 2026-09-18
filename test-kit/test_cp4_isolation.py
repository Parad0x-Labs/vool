"""CP4 — cross-turn and recovery isolation, pure-function side (green at HEAD 091ed83b).

Laws pinned here:
- obligations are session-scoped: two interleaved sessions never serve each other's subject;
- a session with NO obligation inherits nothing, even with byte-identical history text;
- turn identity is provenance-first: a history row keeps its own turn id even when its text
  equals the current question (the text-equality fallback is legacy, not authority).
"""
from __future__ import annotations

from core.context_history_authority import is_current_user_message
from core.live_data_continuation import continuation_inherits_live_data

from kit_lib import GOLD_ANSWER, GOLD_QUESTION, context, gold_thread, plan_operations, weather_thread


def test_two_interleaved_sessions_keep_their_obligations_disjoint():
    """Gold in A, weather in B; each nudge re-asks its OWN session's subject."""
    a = gold_thread("iso-a")
    b = weather_thread("iso-b", ["Kaunas"], "Get weather for Kaunas.")

    ctx_a = context(a, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", "and now?"))
    ctx_b = context(b, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", "and now?"))

    assert plan_operations("and now?", ctx_a) == [("market_quote", "Gold")]
    weather_ops = plan_operations("and now?", ctx_b)
    assert weather_ops == [("weather_lookup", "Kaunas")], weather_ops
    # Neither session's inheritance mentions the other's subject.
    inherited_a = continuation_inherits_live_data("and now?", source_context=ctx_a)
    inherited_b = continuation_inherits_live_data("and now?", source_context=ctx_b)
    assert "kaunas" not in inherited_a.lower()
    assert "gold" not in inherited_b.lower()


def test_a_session_with_no_live_data_past_inherits_nothing():
    """No obligation row and no live-data exchange in THIS session's history: the nudge
    declines. (With a live-data exchange in the session's OWN history the walk-back re-asks
    by design — the obligation row is an accelerator, not the only anchor; recorded here as
    the boundary so a reviewer can challenge it.)"""
    stranger = "openclaw:no-obligation:" + "0" * 20
    source = context(
        stranger,
        ("user", "what is your favourite colour?"), ("assistant", "Blue."),
        ("user", "and now?"),
    )
    assert continuation_inherits_live_data("and now?", source_context=source) == ""
    assert plan_operations("and now?", source) == []


def test_history_walkback_without_an_obligation_row_still_re_asks_the_prior_request():
    """Documented designed behaviour (not a defect claim): the walk-back alone suffices —
    a session whose history holds a live-data exchange re-asks it on a bare nudge even when
    no obligation row was ever recorded (the legacy-lane case). Pinned so any change to
    this boundary is a conscious one."""
    session = "openclaw:walkback-only:" + "1" * 20
    source = context(
        session,
        ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", "and now?"),
    )
    inherited = continuation_inherits_live_data("and now?", source_context=source)
    assert inherited == GOLD_QUESTION, inherited


def test_turn_identity_is_provenance_first_not_text_equality():
    """A recorded user row with its own turn id is NOT the current message, even when its
    text is byte-identical to the current question; the legacy text fallback applies only
    when the row carries no turn id."""
    row = {"role": "user", "content": GOLD_QUESTION, "turn_id": "turn-old"}
    assert not is_current_user_message(
        row, current_user_text=GOLD_QUESTION, current_turn_id="turn-new"
    )
    # Legacy rows without ids still match by text (the fallback the callers rely on).
    legacy = {"role": "user", "content": GOLD_QUESTION}
    assert is_current_user_message(
        legacy, current_user_text=GOLD_QUESTION, current_turn_id="turn-new"
    )
    # An assistant row never matches.
    assistant = {"role": "assistant", "content": GOLD_QUESTION, "turn_id": "turn-old"}
    assert not is_current_user_message(
        assistant, current_user_text=GOLD_QUESTION, current_turn_id="turn-new"
    )


def test_an_intervening_unrelated_request_ends_the_thread():
    """The walk-back break rule: the nearest unrelated real request stops inheritance, so a
    delayed/late obligation cannot resurrect itself across a newer question."""
    session = gold_thread("break")
    source = context(
        session,
        ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER),
        ("user", "write me a python function that sorts a list"), ("assistant", "def sort..."),
        ("user", "and now?"),
    )
    assert continuation_inherits_live_data("and now?", source_context=source) == ""

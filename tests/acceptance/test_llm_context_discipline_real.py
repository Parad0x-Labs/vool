from __future__ import annotations

from apps.vool_agent import ResponseClass
from core.human_input_adapter import adapt_user_input
from core.persistent_memory import append_conversation_event


def test_context_followup_reuses_active_time_context(make_agent) -> None:
    agent = make_agent()

    first = agent.run_once(
        "what time is now in Vilnius?",
        session_id_override="acceptance:vilnius-followup",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "and there?",
        session_id_override="acceptance:vilnius-followup",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert first["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert second["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in second["response"].lower()


def test_stale_person_context_is_dropped_for_math(make_agent) -> None:
    session_id = "acceptance:math-after-person"
    adapt_user_input("who is Toly in Solana?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="who is Toly in Solana?",
        assistant_output="Toly is Anatoly Yakovenko, one of Solana's co-founders.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    agent = make_agent()
    result = agent.run_once(
        "17 * 19",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["response"] == "17 * 19 = 323."
    assert "toly" not in result["response"].lower()

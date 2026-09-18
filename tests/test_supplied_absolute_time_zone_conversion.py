"""A supplied absolute wall time converts between named zones exactly, or the lane declines.

Measured through the real seam 2026-08-15: "What time is it in Tokyo when it is 3:00 PM on a
Tuesday in New York during standard time?" was claimed by `date_time_fast_path` and answered
"Current time is 13:24 EEST" -- the MACHINE's local clock, under a tool-attributed footer, for a
question about two other places and a stipulated instant. (On the served surface the family also
reached weak local models and came back empty.)

The invariant: once the conversion construction is recognized, the lane either converts exactly
(honoring "standard time" as the zone's standard UTC offset and rolling the weekday across
midnight) or declines -- the current clock is the one answer that is always wrong for it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest

from core.agent_runtime.fast_paths_utility import (
    date_time_fast_path,
    supplied_absolute_time_conversion,
    supplied_clock_frame_binding,
)

MEASURED_TURN = (
    "what time is it in tokyo when it is 3:00 pm on a tuesday in new york during standard time?"
)

#: Deterministic reference instants for the dated (non-regime) path -- both Wednesdays.
SUMMER = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
WINTER = datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)


def _answer(text: str, now_utc: datetime | None = None) -> str | None:
    return date_time_fast_path(
        None,
        text,
        source_surface="api",
        session_id="supplied-clock",
        source_context=None,
        now_utc=now_utc,
    )


def test_measured_turn_converts_exactly_with_weekday_rollover() -> None:
    # 3:00 PM Tuesday EST (UTC-5, standard) = 20:00 UTC Tuesday = 5:00 AM Wednesday JST.
    answer = _answer(MEASURED_TURN)
    assert answer == (
        "When it is 3:00 PM on Tuesday in New York (EST, standard time), "
        "it is 5:00 AM on Wednesday in Tokyo (JST)."
    )


def test_clause_order_does_not_change_the_answer() -> None:
    flipped = (
        "when it is 3:00 pm on a tuesday in new york during standard time, "
        "what time is it in tokyo?"
    )
    assert _answer(flipped) == _answer(MEASURED_TURN)


def test_weekday_rolls_backwards_too() -> None:
    # 1:00 AM Wednesday JST = 16:00 UTC Tuesday = 11:00 AM Tuesday EST.
    answer = _answer(
        "what time is it in new york when it is 1:00 am on wednesday in tokyo "
        "during standard time?"
    )
    assert answer == (
        "When it is 1:00 AM on Wednesday in Tokyo (JST, standard time), "
        "it is 11:00 AM on Tuesday in New York (EST)."
    )


def test_regime_stated_on_the_ask_clause_is_honored() -> None:
    # 11:00 PM Monday JST = 14:00 UTC Monday = 2:00 PM Monday GMT (London standard time).
    answer = _answer(
        "it is 11:00 pm on monday in tokyo. what time is it in london during standard time?"
    )
    assert answer == (
        "When it is 11:00 PM on Monday in Tokyo (JST), it is 2:00 PM on Monday in London (GMT)."
    )


def test_undated_conversion_uses_the_real_seasonal_offset() -> None:
    summer = _answer("when it is 3:00 pm in new york, what time is it in tokyo?", SUMMER)
    winter = _answer("when it is 3:00 pm in new york, what time is it in tokyo?", WINTER)
    # August: 3 PM EDT = 19:00 UTC = 4:00 AM JST next day. January: EST -> 5:00 AM next day.
    assert summer == (
        "When it is 3:00 PM in New York (EDT), it is 4:00 AM the next day in Tokyo (JST)."
    )
    assert winter == (
        "When it is 3:00 PM in New York (EST), it is 5:00 AM the next day in Tokyo (JST)."
    )


@pytest.mark.parametrize(
    "turn",
    (
        # Vague supplied time: not exactly computable, and the current clock is always wrong.
        "what time is it in tokyo when it is tuesday afternoon in new york?",
        # No meridiem and no unambiguous 24-hour reading: guessing is the model's habit.
        "what time is it in tokyo when it is 3:00 in new york?",
        # Unresolvable place: recognized construction, no zone -- decline, never local clock.
        "what time is it in tokyo when it is 3:00 pm in narnia?",
        # Tokyo observes no daylight time: the requested regime does not exist there.
        "what time is it in london when it is 3:00 pm in tokyo during daylight time?",
    ),
)
def test_recognized_but_uncomputable_conversions_decline(turn: str) -> None:
    assert supplied_clock_frame_binding(turn)
    assert _answer(turn) is None


def test_current_clock_asks_are_untouched() -> None:
    # Negative controls: the ordinary clock lane behaves exactly as before.
    tokyo_now = _answer("what time is it in tokyo right now")
    assert tokyo_now is not None and tokyo_now.startswith("Current time in Tokyo is ")
    local_now = _answer("what time is it")
    assert local_now is not None and local_now.startswith("Current time is ")
    # A conditional AFTERTHOUGHT in a later sentence is not the conversion construction:
    # the plain "what time is it?" it contains must still get the current clock.
    afterthought = _answer("what time is it? if it is 9:00 pm in london i will call tomorrow")
    assert afterthought is not None and afterthought.startswith("Current time is ")
    assert not supplied_clock_frame_binding(
        "what time is it? if it is 9:00 pm in london i will call tomorrow"
    )
    # A relative-offset clock turn stays in the relative-offset machinery.
    offset = _answer("what time is it in 5h from now in tokyo")
    assert offset is not None and "in 5 hours it will be" in offset


def test_conversion_answers_are_never_the_current_clock() -> None:
    conversions = supplied_absolute_time_conversion(MEASURED_TURN)
    assert conversions is not None
    for shape in conversions:
        assert "current time" not in shape.lower()


def test_real_frontdoor_converts_the_measured_turn_without_a_model(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="supplied-clock", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("supplied-clock conversion reached a model"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)

    result = agent.run_once(
        "What time is it in Tokyo when it is 3:00 PM on a Tuesday in New York "
        "during standard time?",
        session_id_override="openclaw:suppliedclocklane",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "date_time_fast_path"
    assert result["model_calls"] == 0
    assert "5:00 AM on Wednesday in Tokyo (JST)" in result["response"]
    assert "current time" not in result["response"].lower()
    model.assert_not_called()

"""The conversation's record of what the user wants is written from the USER's words only.

The defect this file exists to close
------------------------------------
`adapt_user_input` is not a pure function of its argument. It persists: it records a dialogue turn
with its argument as `raw_input`, derives `current_user_goal` from it, and captures active-mission
slots from it. Two call sites handed it a composed PROMPT instead of an utterance --
`core/agent_runtime/chat_surface.py` and `core/agent_runtime/turn_reasoning.py`, the second on the
adaptive-research path -- because both needed an interpretation that reflected this turn's evidence.

Measured on this branch, three real turns through `/api/chat`::

    U: which local model should i run, qwen3 8b or llama 3.1 8b
    A: Which pair do you want me to compare?  1. ...  2. ...  3. Something else
    U: yes ok my bad, do compare those models

`current_user_goal` afterwards::

    'yes ok my bad, do compare those models Grounding observations for this turn. Use them as
     evidence, not as a template:{"actions_taken":["initial_search", "stop_answer" ],
     "channel": "adaptive_research", "compared_sources": true, "escalated_f...'

`core.bootstrap_context` renders that back into later turns as "Current user goal: ...", so the
runtime's own scaffolding was replayed to the model as a statement of what the person wants -- stale
intent leaking into turns that had nothing to do with it, from a source no reader would suspect.

The split is at PERSISTENCE, not at the text: routing and context selection may legitimately want an
interpretation that reflects the evidence, so `persist=False` derives exactly the same
interpretation and writes nothing.
"""
from __future__ import annotations

import json
import uuid

from core.agent_runtime.chat_surface import observation_prompt
from core.human_input_adapter import adapt_user_input
from storage.dialogue_memory import get_dialogue_session, recent_dialogue_turns


def _sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _observations() -> dict:
    """The shape `chat_surface.observation_prompt` is actually given on the research path."""
    return {
        "actions_taken": ["initial_search", "stop_answer"],
        "channel": "adaptive_research",
        "compared_sources": True,
        "sources": [{"domain": "example.invalid", "title": "Benchmarks", "url": "https://example.invalid/b"}],
    }


def test_an_observation_prompt_does_not_become_the_users_goal():
    session_id = _sid("goal-clean")
    user_text = "yes ok my bad, do compare those models"
    prompt = observation_prompt(user_input=user_text, observations=_observations())

    adapt_user_input(user_text, session_id=session_id)
    adapt_user_input(prompt, session_id=session_id, persist=False)

    goal = str(get_dialogue_session(session_id).get("current_user_goal") or "")
    assert goal, "the user's own turn recorded no goal at all"
    assert "Grounding observations" not in goal, goal
    assert "adaptive_research" not in goal, goal
    assert "{" not in goal and "}" not in goal, f"a JSON payload reached the goal: {goal!r}"


def test_a_non_persisting_adaptation_records_no_turn_and_no_goal():
    """The flag's whole contract, asserted on state rather than on the call.

    Checking that `persist=False` was PASSED would pass just as well if the flag did nothing, which
    is how a guard becomes decorative. The session is read back instead.
    """
    session_id = _sid("no-write")
    prompt = observation_prompt(user_input="compare those two", observations=_observations())

    interpretation = adapt_user_input(prompt, session_id=session_id, persist=False)

    assert interpretation.normalized_text, "the interpretation itself must still be derived"
    assert get_dialogue_session(session_id).get("current_user_goal") in (None, "")
    assert recent_dialogue_turns(session_id, limit=5) == []


def test_the_persisting_default_is_unchanged():
    """The control. If `persist` defaulted to False, every assertion above would pass while the
    conversation quietly stopped remembering anything the user said."""
    session_id = _sid("default-writes")

    adapt_user_input("keep the launch port at 8096", session_id=session_id)

    goal = str(get_dialogue_session(session_id).get("current_user_goal") or "")
    assert "8096" in goal, goal
    assert recent_dialogue_turns(session_id, limit=5), "the default must still record the turn"


def test_a_prompt_adaptation_cannot_overwrite_the_goal_the_user_set():
    """Ordering matters: the research prompt is adapted AFTER the user's turn on the real path, so a
    persisting call there does not merely add noise, it replaces what the user actually said."""
    session_id = _sid("no-overwrite")
    adapt_user_input("compare qwen3 8b against llama 3.1 8b", session_id=session_id)
    before = str(get_dialogue_session(session_id).get("current_user_goal") or "")

    adapt_user_input(
        observation_prompt(user_input="compare qwen3 8b against llama 3.1 8b", observations=_observations()),
        session_id=session_id,
        persist=False,
    )

    assert str(get_dialogue_session(session_id).get("current_user_goal") or "") == before


def test_mission_slots_are_not_captured_from_a_prompt():
    """`capture_active_mission_slots` reads the same argument. A number that exists only inside the
    runtime's own observation payload must never become a mission constraint the user is held to."""
    session_id = _sid("mission")
    observations = dict(_observations())
    observations["result_count"] = 41
    prompt = observation_prompt(user_input="find me a cheap flight", observations=observations)

    adapt_user_input(prompt, session_id=session_id, persist=False)

    # `session_id` itself is a random hex string and will contain any two digits sooner or later --
    # matching against it made this test fail on its own fixture the first time it ran. Only the
    # state the runtime DERIVED is searched.
    session = {
        key: value
        for key, value in (get_dialogue_session(session_id) or {}).items()
        if key != "session_id"
    }
    blob = json.dumps({"session": session, "turns": recent_dialogue_turns(session_id, limit=5)}, default=str)
    assert "41" not in blob, f"a value from the observation payload reached persisted state: {blob[:400]}"

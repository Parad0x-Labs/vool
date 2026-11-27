"""What the person set up is theirs: a reminder answers a question about their own plans, one reminder asked for twice
by two tool calls in one turn is one, and a reminder set "in 40 minutes" is due on their own clock.

iMac gate47 on 6ce598256 (a throwaway profile, qwen3:8b):
- "remind me on Saturday at 6pm to drop off the spare key at Tomas's", then in a new chat "what was I going to bring
  over to his place, and when?" was answered "Web lookups are off": the recall judge read the question against the one
  saved fact, named nothing, and the reminder was never read.
- "set a timer-style reminder for 40 minutes from now to take the bread out" was scheduled by two tool calls in one
  turn and fired twice, and was shown due "02:48:00 UTC" to a +02:00 owner whose profile had no saved zone.

In process. The recall judge is replaced (it is a model); the reminder store, the tools and the clock are real.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

import core.persistent_memory as pm
from core.operator.when import format_due_time
from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _model_call, _now_in_saved_zone, _pending

ZONES = ("Asia/Kolkata", "America/Sao_Paulo", "Europe/Vilnius", "Australia/Adelaide", "Pacific/Auckland")


@pytest.fixture(autouse=True)
def _fresh_runtime_state():
    from core import capability_graph as cg
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    cg.reset()
    yield
    reset_mode_permission_state()
    cg.reset()


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def unsaved_zone_home(tmp_path, monkeypatch):
    """A fresh profile that never saved a zone, on a computer whose clock is in a zone drawn at random."""
    zone = random.Random(str(tmp_path)).choice(ZONES)
    monkeypatch.setenv("TZ", zone)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import load_user_timezone

    assert load_user_timezone() == ""
    return zone


def test_the_owners_zone_is_the_saved_one_then_the_computers_then_utc(monkeypatch) -> None:
    import core.user_preferences as prefs

    monkeypatch.setenv("TZ", "Asia/Kolkata")
    monkeypatch.setattr(prefs, "load_user_timezone", lambda: "Europe/Lisbon")
    assert prefs.owner_timezone() == "Europe/Lisbon"
    monkeypatch.setattr(prefs, "load_user_timezone", lambda: "")
    assert prefs.owner_timezone() == "Asia/Kolkata"
    # A zone the computer names wrongly is not taken on its word.
    monkeypatch.setenv("TZ", "Mars/Olympus_Mons")
    assert prefs.owner_timezone() != "Mars/Olympus_Mons"
    monkeypatch.setattr(prefs, "host_timezone_name", lambda: "")
    assert prefs.owner_timezone() == "UTC"


@pytest.mark.parametrize("minutes", [5, 40, 95])
def test_a_reminder_set_after_a_wait_is_due_on_the_owners_clock(minutes, unsaved_zone_home) -> None:
    zone = unsaved_zone_home
    session = f"chat-wait-{minutes}"
    before = datetime.now().astimezone()
    stored = _model_call("operator.schedule_reminder", {"note": "take the bread out", "in_minutes": minutes},
                         session_id=session)
    assert stored.ok, stored.response_text
    [row] = _pending(session)
    assert row["tz_name"] == zone, row
    due = datetime.fromisoformat(str(row["due_at_utc"]).replace("Z", "+00:00"))
    assert timedelta(minutes=minutes - 2) <= due - before <= timedelta(minutes=minutes + 2), (row, before)
    assert " UTC" not in stored.response_text, stored.response_text


def test_a_stored_offset_zone_is_shown_on_that_offset() -> None:
    assert format_due_time("2026-11-27T02:57:00+00:00", "UTC+05:30").startswith("27 Nov 2026 at 08:27:00")
    assert "UTC-03:00" in format_due_time("2026-11-27T02:57:00+00:00", "UTC-03:00")
    assert "EET" in format_due_time("2026-11-27T02:57:00+00:00", "Europe/Vilnius")


@pytest.mark.parametrize("seed", range(4))
def test_the_same_reminder_sent_twice_in_one_turn_is_one(seed, home) -> None:
    rng = random.Random(f"twice-{seed}")
    session = f"chat-twice-{seed}"
    due = (_now_in_saved_zone() + timedelta(days=rng.randint(1, 5))).replace(
        hour=rng.randint(8, 20), minute=rng.choice((0, 15, 30)), second=0, microsecond=0)
    typed = due.strftime("%Y-%m-%dT%H:%M")
    note = rng.choice(("water the basil", "call the plumber back", "take the bread out"))
    first = _model_call("operator.schedule_reminder", {"note": note, "due_iso": typed}, session_id=session)
    again = _model_call("operator.schedule_reminder", {"note": f"  {note.capitalize()} ", "due_iso": typed},
                        session_id=session)
    assert first.ok and again.ok, (first.response_text, again.response_text)
    assert len(_pending(session)) == 1
    # Anything else is its own reminder: another thing to do, another time, another chat.
    assert _model_call("operator.schedule_reminder", {"note": note + " and the mint", "due_iso": typed},
                       session_id=session).ok
    later = (due + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    assert _model_call("operator.schedule_reminder", {"note": note, "due_iso": later}, session_id=session).ok
    assert len(_pending(session)) == 3
    assert _model_call("operator.schedule_reminder", {"note": note, "due_iso": typed}, session_id=session + "-b").ok
    assert len(_pending(session + "-b")) == 1
    # Asked for again in a later turn, it is set again: the person asked twice.
    assert _call_in_turn("operator.schedule_reminder", {"note": note, "due_iso": typed}, session_id=session,
                         task_id=f"task-later-{seed}").ok
    assert len(_pending(session)) == 4


def _call_in_turn(intent: str, arguments: dict, *, session_id: str, task_id: str):
    """`_model_call` from another turn of the chat."""
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": intent, "arguments": arguments}, task_id=task_id, session_id=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "auto"},
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


QUESTIONS = (
    "what was I going to bring over to his place, and when?",
    "ką turėjau nunešti pas jį ir kada?",
    "was wollte ich ihm vorbeibringen, und wann?",
    "¿qué tenía que llevarle a su casa y cuándo?",
)


def _judge(monkeypatch, pick):
    """The recall judge, replaced: it is handed each list and picks from it as `pick` says."""
    seen: list[list[str]] = []

    def judged(question, items, **kwargs):
        seen.append(list(items))
        return pick(items)

    import core.intent_arbiter as arbiter

    monkeypatch.setattr(arbiter, "arbitrate_recall_facts", judged)
    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *a, **k: True)
    monkeypatch.setattr(pm.memory_entries, "resolve_memory_access_policy", lambda **k: object())
    monkeypatch.setattr(pm, "_held_for_judgement", lambda **k: ["my neighbour Tomas is allergic to peanuts"])
    return seen


def _set_the_key_reminder() -> datetime:
    today = _now_in_saved_zone()
    saturday = (today + timedelta(days=(5 - today.weekday()) % 7 or 7)).replace(hour=18, minute=0, second=0,
                                                                                microsecond=0)
    stored = _model_call("operator.schedule_reminder",
                         {"note": "drop off the spare key at Tomas's", "due_iso": saturday.strftime("%Y-%m-%dT%H:%M")},
                         session_id="chat-a")
    assert stored.ok, stored.response_text
    return saturday


@pytest.mark.parametrize("question", QUESTIONS)
def test_a_question_a_reminder_answers_is_answered_before_a_lookup(question, home, monkeypatch) -> None:
    saturday = _set_the_key_reminder()
    seen = _judge(monkeypatch, lambda items: [n for n, item in enumerate(items, 1) if "spare key" in item])
    answer = pm.held_fact_answer_before_a_lookup(question, session_id="chat-b", source_context={})
    assert answer and "drop off the spare key at Tomas's" in answer, answer
    assert saturday.strftime("%A %d %b %Y at 18:00") in answer, answer
    # The saved facts were read first, then the reminders, each line with its own due day.
    assert seen[0] == ["my neighbour Tomas is allergic to peanuts"]
    assert any("spare key" in line and "18:00" in line for line in seen[1]), seen


def test_a_reminder_the_judge_does_not_name_leaves_the_question_unanswered(home, monkeypatch) -> None:
    _set_the_key_reminder()
    seen = _judge(monkeypatch, lambda items: [])
    assert pm.held_fact_answer_before_a_lookup(QUESTIONS[0], session_id="chat-b", source_context={}) is None
    assert len(seen) == 2


def test_a_private_chats_reminder_is_not_read_from_another_chat(home, monkeypatch) -> None:
    _set_the_key_reminder()
    import core.local_operator_actions as actions

    monkeypatch.setattr(actions, "_is_private_chat", lambda session_id: session_id == "chat-a")
    seen = _judge(monkeypatch, lambda items: [n for n, item in enumerate(items, 1) if "spare key" in item])
    assert pm.held_fact_answer_before_a_lookup(QUESTIONS[0], session_id="chat-b", source_context={}) is None
    assert seen == [["my neighbour Tomas is allergic to peanuts"]]
    # In its own chat it is read.
    assert "spare key" in str(pm.held_fact_answer_before_a_lookup(QUESTIONS[0], session_id="chat-a",
                                                                  source_context={}))


@pytest.mark.parametrize("ask", ["ką turiu padaryti šeštadienį?", "what's on for Saturday evening?"])
def test_a_reply_naming_a_reminder_set_earlier_is_no_claim_of_this_turn(ask, home, monkeypatch) -> None:
    """iMac gate48 on 6a50cd6e5: the right recall answer, from a reminder set two turns before, was read as done and
    replaced with "I have not done that: no step ran on this turn". The judge here answers "done" to anything."""
    from core import intent_arbiter
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    saturday = _set_the_key_reminder()
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    intent_arbiter.bind_turn_judge({}, lambda prompt: '{"answer": "done"}')
    try:
        answer = (f"You asked me to remind you: drop off the spare key at Tomas's, "
                  f"{saturday.strftime('%A %d %b %Y at 18:00')}.")
        kept = enforce_final_action_honesty({"response": answer}, user_input=ask, session_id="chat-b",
                                            source_context={})
        assert kept["response"] == answer, kept
        # A reminder that was never set is still no answer: the claim is corrected.
        made_up = "Done: I set a reminder to water Tomas's plants on Saturday."
        corrected = enforce_final_action_honesty({"response": made_up}, user_input=ask, session_id="chat-b",
                                                 source_context={})
        assert corrected["response"] != made_up
    finally:
        intent_arbiter.bind_turn_judge({}, None)


@pytest.mark.parametrize("hour,minute", [(9, 30), (14, 5), (7, 45), (18, 15)])
def test_a_clock_time_in_an_order_is_no_value_handed_over(hour, minute) -> None:
    """iMac gate48 on 6a50cd6e5: "put a dentist check-up in my calendar for Tuesday at 9:30am" was read as
    "<said>: 30am", saved as a memory fact, and no calendar card came. A colon between two digits is a clock time."""
    from core.memory.everyday_remember import stated_facts

    for said in (f"put a dentist check-up in my calendar for Tuesday at {hour}:{minute:02d}",
                 f"pop the vet visit into my diary on Monday at {hour}:{minute:02d}am",
                 f"stick my piano lesson on the calendar for Friday {hour}:{minute:02d}"):
        assert stated_facts(said) == [], said
    # The colon that hands a value over still does, digits after it or not.
    assert stated_facts(f"my locker number at the pool: {hour}{minute:02d}")
    assert stated_facts("the gym gave me a new table tennis rating this season: 1276.")

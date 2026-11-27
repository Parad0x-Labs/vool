"""A reminder order in any words and any language runs the way "remind me ..." runs; a question about reminders sets none.

Cut-list 29 (Mac, qwen3:8b): a reminder asked without the words "remind me" went through the model, a Manual-mode card
and a resume, 70-84 s, while "remind me" was set in about 3 s by the chat lane, with no card. The model's typed call is
now read against the message it came from (core.operator.reminder_order): a call whose time and note are the message's
own, in a message that is no question, is the person's order and runs as the chat lane runs it, its confirmation the
answer. Cut-list 56 (gate 51 case 6): "kada turiu paskambinti Eglei?" ("when do I have to call Egle?") was typed as a
new daily reminder due now; a question whose call carries no time of its own is answered from the reminder list, and an
order with no time is never set for now.

The shape readings are in process with words, days and hours drawn at random; the served rows drive /api/chat with a
scripted model (no network, no real model), as tests/test_an_allowed_reminder_in_another_language_is_set.py does.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.operator.reminder_order import NO_TIME, ORDER, RECALL, reminder_call_shape

_RNG = random.Random(20261127)

#: Orders that never say "remind": (message, typed note). {h} is the hour said, {m} a minute when one is said.
ORDERS = (
    ("don't let me forget to {note} tomorrow at {h}", "{note}"),
    ("ping me at {h}:{m} so I {note}", "{note}"),
    ("tomorrow {h}pm I need to {note}, make sure I do", "{note}"),
    ("rytoj {h} val. {lt}, nepamiršk man pasakyti", "{lt}"),
    ("poryt {h}:{m} turiu {lt}", "{lt}"),
    ("penktadienį {h} val. {lt}", "{lt}"),
    ("Sag mir morgen um {h} Uhr Bescheid: {de}", "{de}"),
    ("Am Freitag um {h}:{m} {de}, bitte nicht vergessen", "{de}"),
    ("Gib mir in {w} Minuten ein Zeichen, {de}", "{de}"),
)
EN = ("call the plumber", "water the balcony plants", "pay the electricity bill", "pick up the parcel")
LT = ("paskambinti santechnikui", "palaistyti pomidorus", "sumokėti už internetą", "paimti siuntinį")
DE = ("den Zahnarzt anrufen", "die Mülltonne rausstellen", "das Paket abholen", "Oma anrufen")

#: Questions about reminders already set, each with the call a small model typed for it: no time of its own.
QUESTIONS = (
    ("kada turiu paskambinti Eglei?", "paskambinti Eglei"),
    ("when was I supposed to call the plumber?", "call the plumber"),
    ("Wann sollte ich nochmal den Zahnarzt anrufen?", "den Zahnarzt anrufen"),
    ("ar nustatei man ką nors dėl siuntinio?", "siuntinio"),
)


def _now_iso(offset_minutes: int = 0) -> str:
    from zoneinfo import ZoneInfo

    from core.time_authority import CLOCK
    from core.user_preferences import owner_timezone

    return (CLOCK.now_utc().astimezone(ZoneInfo(owner_timezone())) + timedelta(minutes=offset_minutes)).strftime(
        "%Y-%m-%dT%H:%M")


def _order(seed: int) -> tuple[str, dict]:
    rng = random.Random(f"order-{seed}")
    template, note_template = ORDERS[seed % len(ORDERS)]
    hour, minute, wait = rng.randint(7, 11), rng.choice((15, 30, 45)), rng.choice((10, 20, 45))
    parts = {"note": rng.choice(EN), "lt": rng.choice(LT), "de": rng.choice(DE)}
    text = template.format(h=hour, m=minute, w=wait, **parts)
    note = note_template.format(**parts)
    day = (datetime.now() + timedelta(days=rng.randint(1, 6))).date().isoformat()
    if "{w}" in template:
        return text, {"note": note, "in_minutes": wait}
    said_minute = minute if "{m}" in template else 0
    clock_hour = hour + 12 if "pm" in template else hour
    return text, {"note": note, "due_iso": f"{day}T{clock_hour:02d}:{said_minute:02d}"}


@pytest.mark.parametrize("seed", range(18))
def test_an_order_in_any_words_reads_as_the_persons_order(seed) -> None:
    text, arguments = _order(seed)
    assert "remind" not in text.lower()
    assert reminder_call_shape(text, arguments) == ORDER, (text, arguments)


@pytest.mark.parametrize("seed", range(6))
def test_a_time_or_note_the_message_never_said_is_not_its_order(seed) -> None:
    text, arguments = _order(seed)
    other = dict(arguments)
    if "due_iso" in other:
        other["due_iso"] = other["due_iso"][:11] + "03:07"
    else:
        other["in_minutes"] = 997
    assert reminder_call_shape(text, other) != ORDER, (text, other)
    assert reminder_call_shape(text, {**arguments, "note": "renew the passport"}) != ORDER, text


@pytest.mark.parametrize("question, note", QUESTIONS)
def test_a_question_about_reminders_is_a_recall_whatever_the_call_typed(question, note) -> None:
    for typed in ({"note": note, "due_iso": _now_iso(), "repeat": "daily"}, {"note": note}, {"note": note, "day": "friday",
                                                                                              "due_iso": _now_iso(90)}):
        assert reminder_call_shape(question, typed) == RECALL, (question, typed)


@pytest.mark.parametrize("text", ("paskambinti Eglei", "den Zahnarzt anrufen nicht vergessen", "make sure I call the bank"))
def test_an_order_with_no_time_is_never_set_for_now(text) -> None:
    assert reminder_call_shape(text, {"note": text, "due_iso": _now_iso()}) == NO_TIME
    assert reminder_call_shape(text, {"note": text}) == NO_TIME


def test_a_polite_question_with_its_own_time_keeps_the_ordinary_path() -> None:
    # Said as a question with a time, it may be an order or a question about that time: neither shortcut is taken.
    assert reminder_call_shape("gal gali rytoj 9 val. priminti paskambinti mamai?",
                               {"note": "paskambinti mamai", "due_iso": "2026-12-01T09:00"}) == ""


# ---- the permission seam: the order runs without a card where the chat lane runs it, nowhere else -------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    from tests.pa_beta_gate._pc_calendar_rig import prepare_home

    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _fresh_runtime_state():
    from core import capability_graph as cg
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    cg.reset()
    yield
    reset_mode_permission_state()
    cg.reset()


def _context(session: str, mode: str, **extra) -> dict:
    return {"surface": "openclaw", "platform": "openclaw", "operating_mode": mode, "runtime_session_id": session,
            "session_id": session, "turn_id": f"turn-{session}", "_owner_local": True, **extra}


def _call(arguments: dict, context: dict, task_id: str):
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": "operator.schedule_reminder", "arguments": arguments}, task_id=task_id,
        session_id=str(context["runtime_session_id"]), source_context=context,
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


def _rows(session: str) -> list[dict]:
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id=session, get_connection_fn=get_connection)


@pytest.mark.parametrize("seed", range(3))
def test_a_manual_mode_order_runs_without_a_card_and_nothing_else_does(seed, home) -> None:
    from core.mode_permission_policy import revoke_internal_authority
    from core.operator.reminder_order import order_runs_as_the_person_said_it

    rng = random.Random(f"manual-{seed}")
    note = rng.choice(LT)
    text = f"rytoj {rng.randint(8, 11)} val. {note}"
    hour = int(text.split()[1])
    due = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d") + f"T{hour:02d}:00"
    arguments = {"note": note, "due_iso": due}
    session, task_id = f"chat-direct-{seed}", f"task-direct-{seed}"
    context = _context(session, "manual")

    # The same call without the person's order behind it still asks first, in Manual mode.
    asked = _call(arguments, context, task_id)
    assert asked.status == "pending_approval" and _rows(session) == []

    token = order_runs_as_the_person_said_it("operator.schedule_reminder", arguments, text=text, task_id=task_id,
                                             source_context=context, more_calls_in_the_reply=False)
    assert token
    try:
        # Bound to this task and chat: another task or another chat still asks.
        assert _call(arguments, {**context, "internal_authority_token": token}, "task-other").status == "pending_approval"
        stored = _call(arguments, {**context, "internal_authority_token": token}, task_id)
    finally:
        revoke_internal_authority(token)
    assert stored.ok and stored.mode == "tool_executed", stored.response_text
    assert [row["note"] for row in _rows(session)] == [note]


def test_no_order_token_for_a_question_a_second_call_a_remote_turn_or_plan_mode(home) -> None:
    from core.operator.reminder_order import order_runs_as_the_person_said_it

    due = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d") + "T09:00"
    order = ("rytoj 9 val. paskambinti mamai", {"note": "paskambinti mamai", "due_iso": due})

    def token(text, arguments, context, *, more=False):
        return order_runs_as_the_person_said_it("operator.schedule_reminder", arguments, text=text, task_id="task-x",
                                                source_context=context, more_calls_in_the_reply=more)

    assert not token("kada turiu paskambinti Eglei?", {"note": "paskambinti Eglei", "due_iso": _now_iso()},
                     _context("chat-q", "manual"))
    assert not token(*order, _context("chat-batch", "manual"), more=True)
    assert not token(*order, _context("chat-plan", "plan"))
    assert not token(*order, _context("chat-remote", "manual", _owner_local=False, _remote_device="phone-1"))


# ---- served, end to end --------------------------------------------------------------------------------------------

def _served_turns(provider, turns: list[tuple[str, str]]):
    from pathlib import Path

    from tests._blackbox_served_rig import ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import SEED
    from tests.test_an_allowed_reminder_in_another_language_is_set import _turn
    from tests.test_private_chat_served_workflow import MODEL, _certify

    repo = Path(__file__).resolve().parents[1]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp, provider:
        home = Path(tmp) / "home"
        run_in_home(home, SEED.format(root=repo, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"}
        with ServedDaemon(home, env_extra=env) as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            events = []
            for text, _ in turns:
                provider.arguments = dict(_)
                events.append(_turn(daemon, text, session, uuid.uuid4().hex))
            rows = json.loads(run_in_home(home, (
                "import json\n"
                "from core.operator.reminders import list_reminders\n"
                "from storage.db import get_connection\n"
                f"print(json.dumps(list_reminders(session_id={session!r}, get_connection_fn=get_connection)))\n"
            )).strip().splitlines()[-1])
    return events, rows


def _provider():
    from tests.test_an_allowed_reminder_in_another_language_is_set import _ReminderProvider

    class _Scripted(_ReminderProvider):
        """The judge picks Reminders; the model types the call set for the turn; a round after a tool counts."""

        def __init__(self) -> None:
            super().__init__("", "")
            self.arguments: dict = {}
            self.rounds_after_a_tool = 0

        def reply(self, model, body):
            text = json.dumps(body, ensure_ascii=False)
            if "vool_probe" in text or "probe." in text:
                return super().reply(model, body)
            if "The assistant has the tools below" in text:
                return '{"answer": [1]}'
            if any(str((m or {}).get("role")) == "tool" for m in body.get("messages") or []):
                self.rounds_after_a_tool += 1
                return "Gerai."
            tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])
                     if isinstance(t, dict)]
            name = next((tool for tool in tools if "schedule_reminder" in tool), "")
            if name:
                self.offered_reminder_tool += 1
                return {"content": "", "tool_calls": [
                    {"id": f"call-{uuid.uuid4().hex[:6]}", "type": "function",
                     "function": {"name": name, "arguments": self.arguments}}]}
            return super().reply(model, body)

    return _Scripted()


def test_served_an_order_without_remind_is_set_with_no_card_and_a_question_sets_nothing() -> None:
    note = _RNG.choice(LT)
    hour = _RNG.randint(8, 11)
    due = (datetime.now() + timedelta(days=1)).replace(hour=hour, minute=0).strftime("%Y-%m-%dT%H:%M")
    provider = _provider()
    order = (f"rytoj {hour} val. {note}, nepamiršk", {"note": note, "due_iso": due})
    question = ("kada turiu paskambinti Eglei?",
                {"note": "paskambinti Eglei", "due_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                 "repeat": "daily"})
    events, rows = _served_turns(provider, [order, question])

    set_turn, question_turn = events
    assert not [e for e in set_turn if e.get("type") == "permission.required"], [e.get("type") for e in set_turn]
    assert [row["note"] for row in rows if row["status"] == "scheduled"] == [note], rows
    assert not [e for e in question_turn if e.get("type") == "permission.required"]
    # The order's own confirmation is its answer: the model was asked after a tool only for the question's recall.
    assert provider.rounds_after_a_tool <= 1, provider.rounds_after_a_tool

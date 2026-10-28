"""Job 6: the model can set and cancel this runtime's own reminders.

Before it, only the chat parser made a reminder: when its wording missed ("i need a reminder
to pay the rent on the 3rd"), the model had no reminder tool, so it could only say "done" with
nothing stored. ``operator.schedule_reminder`` and ``operator.cancel_reminder`` each take
``{"text": "the request as written"}`` (like ``operator.save_note``) and reach the SAME
handlers the chat lane reaches (the ``schedule_reminder`` / ``cancel_reminder`` dispatch kinds
in core/local_operator_actions.py): the same when-reading, the same past-time refusal, the same
reminder-id resolution, the same durable store.

In process only, like tests/test_note_tool_calls_reach_their_handlers.py: the executor the
served tool loop calls (``core.tool_intent_executor.execute_tool_intent``) runs directly, no
daemon is started and no call leaves the process (LABELLED: no model, no network, a tmp VOOL
home per test through the product-completion rig).

Auto mode asks before a delete: the cancel rows allow that exact request through the
controller's own resolve seam, exactly as the chat page's Allow does
(tests/test_approved_commands_run_in_the_served_path.py). An approval is never routed around.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.capability_graph import model_visible_specs
from core.mode_permission_policy import PermissionAction, actions_for_tool
from core.operator.parser import parse_operator_action_intent
from core.tool_demand_signals import owner_local_operator_intents
from tests.pa_beta_gate._pc_calendar_rig import prepare_home

#: Reminder asks the chat parser misses but the wording seat catches (the shapes the model
#: tool exists for). Every row is verified: parse returns no schedule_reminder, the seat returns it.
PARSER_MISSED_ASKS = (
    # The three shapes named in the job packet.
    "i need a reminder to pay the rent on the 3rd",
    "I want a reminder for the show tickets tomorrow at 10",
    "nudge me to stretch at 5pm",
    # More everyday wordings of the same ask.
    "I need a reminder to take my medication at 8am",
    "i want a reminder for the laundry tomorrow at noon",
    "need a reminder to renew the passport next month",
    "can you nudge me to check the oven in 20 minutes",
    "I want a reminder for my stretching break at 5",
    "nudge me about the parking meter in 45 minutes",
    "I need a reminder to charge my earbuds tonight",
    "nudge me to water the fern on friday at 8",
    "I need a reminder to book the dentist on monday at 9",
    "want a reminder to submit the timesheet on friday at 4",
)

#: Sentences about reminders that are not asks to store one: none may seat a reminder intent.
NOT_ASKS = (
    # The shapes named in the job packet.
    "the reminder I set never fired",
    "my reminder sound is annoying",
    "which reminders app is best for Android right now?",
    "I love getting reminders in the morning",
    "reminders help me stay on track",
    "she reminded me about the party",
    "the meeting reminder went off early",
    "can reminders sync between devices?",
    # Recall, not storage: mandated quiet row (a reminder ask never seats for it).
    "remind me what I said about the May trip",
    # More ordinary prose of our own.
    "reminders on my phone keep duplicating",
    "I set a reminder once and it never went off",
    "do you think reminders make people anxious?",
    "the reminder sound woke the baby",
    "my reminder list is way too long",
    "she set a reminder for the cake",
    "I hate the default reminder sound",
    "a reminder popped up during the call",
)

#: Cancel wordings that seat the cancel tool at the wording level.
CANCEL_WORDINGS = (
    "cancel my rent reminder",
    "cancel that reminder",
    "delete the dentist reminder please",
    "remove my 9am reminder",
)

#: The seat-level owner-local/remote split, as in tests/test_operator_wording_seats_owner_local.py.
OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-reminder-seat", "turn_id": "t-reminder-seat"}
REMOTE = {"_owner_local": False, "runtime_session_id": "s-reminder-remote", "turn_id": "t-reminder-remote"}

#: One ask per new tool: proving both seats end to end through the offer seam.
OFFER_ASKS = {
    "i need a reminder to pay the rent on the 3rd": "operator.schedule_reminder",
    "cancel my rent reminder": "operator.cancel_reminder",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _fresh_runtime_state():
    """Approvals and capability-graph caches never leak between tests."""
    from core import capability_graph as cg
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    cg.reset()
    yield
    reset_mode_permission_state()
    cg.reset()


def _model_call(intent: str, arguments: dict, *, session_id: str, approval_token: str = ""):
    """The executor the served tool loop calls, exactly as tests/test_note_tool_calls... builds it."""
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    context = {"surface": "openclaw", "platform": "openclaw", "operating_mode": "auto"}
    if approval_token:
        context["mode_approval_token"] = approval_token
    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        task_id="task-reminder-tools",
        session_id=session_id,
        source_context=context,
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


def _allow(execution) -> str:
    """Allow the exact pending request the controller raised, as the chat page's Allow does."""
    from core.mode_permission_policy import resolve_approval

    request = dict((execution.details or {}).get("approval_request") or {})
    token = str(request.get("token") or request.get("approval_id") or "")
    assert token and resolve_approval(token, decision="allow"), execution.details
    return token


def _model_call_allowed(intent: str, arguments: dict, *, session_id: str):
    """A call that ends executed even where auto mode asks first: allow, then re-issue it."""
    execution = _model_call(intent, arguments, session_id=session_id)
    if execution.status == "pending_approval":
        execution = _model_call(
            intent, arguments, session_id=session_id, approval_token=_allow(execution)
        )
    return execution


def _pending(session_id: str) -> list[dict]:
    """The rows the handlers' own store seam holds for this chat."""
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id=session_id, get_connection_fn=get_connection)


def _now_in_saved_zone() -> datetime:
    """The same instant _scheduling_now reads: the clock in the zone the rig saved."""
    from core.time_authority import CLOCK
    from core.user_preferences import load_user_timezone

    return CLOCK.now_for_timezone(load_user_timezone() or "UTC")


def test_a_model_call_stores_one_reminder(home) -> None:
    session = "chat-reminder-tools-one"
    before = _now_in_saved_zone()

    stored = _model_call("operator.schedule_reminder", {"text": "remind me to water the plants tomorrow at 9:00"}, session_id=session)

    after = _now_in_saved_zone()
    assert stored.ok and stored.mode == "tool_executed", stored.response_text
    assert "water the plants" in stored.response_text, stored.response_text

    [row] = _pending(session)
    # The subject is the thing to do: the trailing clock clause is stripped
    # (_reminder_note_from_text); the bare day word before it stays, exactly as for
    # "remind me to call mom at 5pm" -> "call mom" in tests/pa_beta_gate/test_reminder_time_reading.py.
    assert row["note"] == "water the plants tomorrow", row["note"]
    assert stored.details["note"] == row["note"]
    assert stored.details["reminder_id"] == row["reminder_id"]
    assert stored.details["due_at_utc"] == row["due_at_utc"]

    # Due tomorrow at 09:00 in the zone the rig saved (the same wall clock the handler read).
    wall = datetime.fromisoformat(str(row["due_wall"]))
    assert wall.tzinfo is not None and (wall.hour, wall.minute) == (9, 0), row["due_wall"]
    assert wall.date() in {(before + timedelta(days=1)).date(), (after + timedelta(days=1)).date()}, row["due_wall"]
    # The two stored instants name the same moment.
    due_utc = datetime.fromisoformat(str(row["due_at_utc"]))
    assert due_utc == wall.astimezone(due_utc.tzinfo), (row["due_at_utc"], row["due_wall"])


def test_cancel_removes_only_the_named_reminder(home) -> None:
    session = "chat-reminder-tools-two"
    bins = _model_call("operator.schedule_reminder", {"text": "remind me tomorrow at 8:00 to take the bins out"}, session_id=session)
    plants = _model_call("operator.schedule_reminder", {"text": "remind me to water the plants at 9:00 tomorrow"}, session_id=session)
    assert bins.ok and plants.ok, (bins.response_text, plants.response_text)
    # Both when-shapes (leading and trailing) store the bare subject.
    assert {row["note"] for row in _pending(session)} == {"take the bins out", "water the plants"}

    listed = _model_call("operator.list_reminders", {}, session_id=session)
    assert listed.ok and listed.mode == "tool_executed", listed.response_text
    rows = listed.details["reminders"]
    target = next(row for row in rows if row["note"] == "take the bins out")
    kept = next(row for row in rows if row["note"] == "water the plants")
    short_id = str(target["reminder_id"])[:8]

    # Auto mode asks before a delete; nothing is cancelled until the owner allows it.
    asked = _model_call("operator.cancel_reminder", {"text": f"cancel the reminder id {short_id}"}, session_id=session)
    assert asked.status == "pending_approval", asked.response_text
    assert len(_pending(session)) == 2

    cancelled = _model_call(
        "operator.cancel_reminder",
        {"text": f"cancel the reminder id {short_id}"},
        session_id=session,
        approval_token=_allow(asked),
    )
    assert cancelled.ok and cancelled.mode == "tool_executed", cancelled.response_text
    assert "take the bins out" in cancelled.response_text, cancelled.response_text
    remaining = _pending(session)
    assert [row["reminder_id"] for row in remaining] == [kept["reminder_id"]]

    # "cancel that reminder" resolves the only pending reminder left, through the same allow.
    that = _model_call_allowed("operator.cancel_reminder", {"text": "cancel that reminder"}, session_id=session)
    assert that.ok and that.mode == "tool_executed", that.response_text
    assert "water the plants" in that.response_text, that.response_text
    assert _pending(session) == []


def test_a_past_time_stores_nothing(home) -> None:
    session = "chat-reminder-tools-past"

    refused = _model_call("operator.schedule_reminder", {"text": "remind me to email sarah yesterday at 9:00"}, session_id=session)

    assert not refused.ok and "already passed" in refused.response_text, refused.response_text
    assert refused.details.get("reason") == "due_time_passed", refused.details
    assert _pending(session) == []


@pytest.mark.parametrize(("text", "tool"), sorted(OFFER_ASKS.items()))
def test_remote_and_no_context_get_neither_tool(text, tool) -> None:
    def offered(context) -> set[str]:
        return {str(spec.get("intent") or "") for spec in model_visible_specs(user_text=text, source_context=context)}

    assert tool in offered(dict(OWNER_LOCAL)), f"the owner-local turn did not seat {tool}"

    remote = offered(dict(REMOTE))
    assert "operator.schedule_reminder" not in remote and "operator.cancel_reminder" not in remote, sorted(remote)

    without_context = offered(None)
    assert "operator.schedule_reminder" not in without_context and "operator.cancel_reminder" not in without_context, sorted(without_context)


def test_the_permission_class_is_not_an_external_send() -> None:
    assert set(actions_for_tool("operator.schedule_reminder")) == {PermissionAction.CREATE_FILES}
    assert set(actions_for_tool("operator.cancel_reminder")) == {PermissionAction.DELETE_FILES}
    for name in ("operator.schedule_reminder", "operator.cancel_reminder"):
        actions = set(actions_for_tool(name))
        assert PermissionAction.SEND_EXTERNAL_MESSAGES not in actions, name
        assert PermissionAction.ACCESS_EXTERNAL_PROVIDERS not in actions, name


@pytest.mark.parametrize("text", PARSER_MISSED_ASKS)
def test_parser_missed_reminder_asks_seat_the_tool(text) -> None:
    parsed = parse_operator_action_intent(text)
    assert parsed is None or parsed.kind != "schedule_reminder", text
    assert "operator.schedule_reminder" in owner_local_operator_intents(text), text


@pytest.mark.parametrize("text", NOT_ASKS)
def test_sentences_about_reminders_that_are_not_asks_seat_nothing(text) -> None:
    seated = owner_local_operator_intents(text)
    assert not [intent for intent in seated if "reminder" in intent], (text, seated)


@pytest.mark.parametrize("text", CANCEL_WORDINGS)
def test_cancel_wordings_seat_the_cancel_tool(text) -> None:
    assert "operator.cancel_reminder" in owner_local_operator_intents(text), text

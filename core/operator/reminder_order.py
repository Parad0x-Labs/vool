"""What a reminder call the model typed is, read from the shape of the person's message and the call's own arguments.

A reminder asked with the words "remind me" is read by the chat parser and set at once, with no card: the person's own
words are the order (core.agent_runtime.turn_dispatch, the operator lane). Any other wording, and any other language,
went to the model: the turn's judge picked Reminders, the model typed operator.schedule_reminder, Manual mode showed a
card, the allowed call resumed the turn and the model wrote the reply. On the Mac (qwen3:8b) that was 70-84 s against
about 3 s for "remind me", for the same reminder at the same time.

The model's typed call is the judgement that the message asks for a reminder; what is read here is whether its values
are the message's own. That is a shape, the same in every language:

* the due time the call typed is said in the message: its clock hour (and minutes, when not on the hour) or its wait
  are numbers the message holds, or VOOL's when-reader reads a time out of the words;
* the note the call typed is in the message: each of its words of four letters or more is a word of the message, or
  shares its first four letters (an inflected form);
* the message is not a question: it does not end in a question mark of any script.

A call that is all three is the person's order, as the parser lane reads one, and runs the way that lane runs it:
without a card, its own confirmation the answer. A question whose call carries no time of its own asks about
reminders already set ("kada turiu paskambinti Eglei?", cut-list 56: typed as a new daily reminder due now) and is
answered from the reminder list. An order whose call carries no time of its own and a due time at the current moment
is a time the model made up: nothing is set and the model is told to ask when. Anything else goes on as before.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

#: The message reads as an order: the call may run as the person's own words.
ORDER = "order"
#: The message is a question that carries no time of its own: it asks about reminders already set.
RECALL = "recall"
#: The message gives no time and the call's time is the current moment: the model made it up.
NO_TIME = "no_time"

#: A question mark in the scripts VOOL's languages write ("?", fullwidth "？", Arabic "؟"), closing the message with only
#: marks, spaces or emoji after it; or the Spanish opening mark anywhere.
_QUESTION_RE = re.compile(r"[?？؟][^\w]*$|¿", re.UNICODE)
_DIGITS_RE = re.compile(r"(?<!\d)\d{1,4}(?!\d)")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_CLOCK_RE = re.compile(r"T(\d{1,2}):(\d{2})")
#: How near the current moment a typed due time is read as "now".
_NOW_WINDOW = timedelta(minutes=10)


def is_question(text: str) -> bool:
    """Whether the message is written as a question (by its punctuation, in any script)."""
    return bool(_QUESTION_RE.search(str(text or "").strip()))


def _numbers(text: str) -> set[int]:
    return {int(match) for match in _DIGITS_RE.findall(str(text or ""))}


def _fold(word: str) -> str:
    return unicodedata.normalize("NFC", word).casefold()


def note_is_in_the_message(note: str, text: str) -> bool:
    """Whether every word of four letters or more of the typed note is a word of the message, or shares its first four
    letters with one; a note with no such word is read whole, as words of the message."""
    message = [_fold(word) for word in _WORD_RE.findall(str(text or ""))]
    note_words = [_fold(word) for word in _WORD_RE.findall(str(note or ""))]
    if not note_words or not message:
        return False
    long_words = [word for word in note_words if len(word) >= 4]
    if not long_words:
        return all(word in message for word in note_words)
    return all(any(other[:4] == word[:4] and len(other) >= 4 for other in message) for word in long_words)


def _typed_minutes(arguments: dict[str, Any]) -> int | None:
    raw = arguments.get("in_minutes")
    if raw is None or isinstance(raw, bool) or str(raw).strip() == "":
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    return int(value) if value == int(value) and value >= 1 else None


def _typed_clock(arguments: dict[str, Any]) -> tuple[int, int] | None:
    match = _CLOCK_RE.search(str(arguments.get("due_iso") or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return (hour, minute) if hour < 24 and minute < 60 else None


def _time_said_in_the_message(text: str, arguments: dict[str, Any]) -> bool:
    """Whether the due time the call typed is said in the message: its numbers are the message's, or VOOL's
    when-reader reads a clock time or a wait from the words."""
    numbers = _numbers(text)
    minutes = _typed_minutes(arguments)
    if minutes is not None:
        said = {minutes}
        if minutes % 60 == 0:
            said.add(minutes // 60)
        if minutes % 1440 == 0:
            said.add(minutes // 1440)
        if said & numbers:
            return True
    clock = _typed_clock(arguments)
    if clock is not None and minutes is None:
        hour, minute = clock
        hours = {hour, hour % 12 or 12}
        if hours & numbers and (minute == 0 or minute in numbers):
            return True
    # A time said in words ("in three quarters of an hour", "at noon") counts when VOOL's when-reader reads from them
    # the same time the call typed.
    try:
        from core.operator.when import normalize_time_words, parse_when_expression

        read = parse_when_expression(normalize_time_words(str(text or "")))
        if not read.ok:
            return False
        if minutes is not None:
            from core.time_authority import CLOCK

            due = datetime.fromisoformat(str(read.due_at_utc).replace("Z", "+00:00"))
            expected = CLOCK.now_utc().astimezone(timezone.utc) + timedelta(minutes=minutes)
            return abs(due - expected) <= timedelta(minutes=2)
        wall = _CLOCK_RE.search(str(read.due_wall or ""))
        return clock is not None and wall is not None and (int(wall.group(1)), int(wall.group(2))) == clock
    except Exception:
        return False


def _typed_due_is_now(arguments: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether the call typed no due time at all, or one at the current moment (on the owner's clock)."""
    if _typed_minutes(arguments) is not None:
        return False
    typed = str(arguments.get("due_iso") or "").strip()
    if not typed:
        return True
    try:
        moment = datetime.fromisoformat(typed.replace("Z", "+00:00"))
    except ValueError:
        return False
    try:
        from zoneinfo import ZoneInfo

        from core.time_authority import CLOCK
        from core.user_preferences import owner_timezone

        zone = ZoneInfo(owner_timezone())
        current = now or CLOCK.now_utc()
    except Exception:
        zone, current = timezone.utc, now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return abs(moment - current) <= _NOW_WINDOW


def reminder_call_shape(text: str, arguments: dict[str, Any] | None, *, now: datetime | None = None) -> str:
    """ORDER, RECALL, NO_TIME, or "" for a call to leave as it is (see the module docstring)."""
    message = str(text or "").strip()
    args = dict(arguments or {})
    if not message:
        return ""
    timed = _time_said_in_the_message(message, args)
    if is_question(message):
        return "" if timed else RECALL
    if not timed:
        return NO_TIME if _typed_due_is_now(args, now) else ""
    note = str(args.get("note") or "").strip()
    return ORDER if note and note_is_in_the_message(note, message) else ""


#: What the model is told when the call's time is not the message's and is the current moment.
NO_TIME_CORRECTION = (
    "Nothing was set: this message gives no time for a reminder, and a reminder is never set for now on its own. "
    "If the message asks about a reminder already set, call operator.list_reminders. If it asks for a new reminder, "
    "ask the person when to remind them, in their language."
)


def no_time_refusal(intent: str) -> Any:
    from core.execution.models import ToolIntentExecution, _tool_observation

    return ToolIntentExecution(
        handled=True,
        ok=False,
        status="invalid_arguments",
        mode="refused",
        tool_name=intent,
        response_text=NO_TIME_CORRECTION,
        user_safe_response_text=NO_TIME_CORRECTION,
        details={"executed": False, "reason": "reminder_time_not_in_message", "observation": _tool_observation(
            intent=intent, tool_surface="operator", ok=False, status="invalid_arguments",
        )},
    )


def order_runs_as_the_person_said_it(
    intent: str,
    arguments: dict[str, Any],
    *,
    text: str,
    task_id: str,
    source_context: dict[str, Any] | None,
    more_calls_in_the_reply: bool,
) -> str:
    """The internal-authority token under which this reminder call runs without a card, the way the chat lane runs the
    same order typed as "remind me ...", or "" when it does not qualify.

    Only for a call whose shape is ORDER, alone in its reply, on a turn that is the owner's own at this computer and is
    one request, in a mode that permits the write at all: the scope covers exactly this intent, this task and this
    chat for a minute, and the controller's own decision still records the allow (granted_by internal_scope)."""
    if intent != "operator.schedule_reminder" or more_calls_in_the_reply:
        return ""
    if reminder_call_shape(text, arguments) != ORDER:
        return ""
    context = dict(source_context or {})
    try:
        from core.request_trust import REMOTE_DEVICE_KEY, turn_is_remote

        if turn_is_remote(context) or context.get(REMOTE_DEVICE_KEY):
            return ""
        from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

        if not lane_may_claim_whole_turn(text, "operator_action_dispatch"):
            return ""
        from core.mode_permission_policy import (
            PermissionAction,
            PermissionEffect,
            grant_internal_authority,
            preview_tool_call,
        )

        preview = preview_tool_call(intent=intent, arguments=arguments, task_id=task_id, source_context=context)
        if preview.effect is PermissionEffect.DENY:
            return ""
        if set(preview.actions) != {PermissionAction.CREATE_FILES}:
            return ""
        session_id = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
        if not session_id or not str(task_id or "").strip():
            return ""
        return grant_internal_authority(
            label="the person's own reminder order",
            actions={PermissionAction.CREATE_FILES},
            duration_seconds=60,
            session_id=session_id,
            task_id=str(task_id),
            intents={intent},
        )
    except Exception:
        return ""


__all__ = [
    "NO_TIME",
    "NO_TIME_CORRECTION",
    "ORDER",
    "RECALL",
    "is_question",
    "no_time_refusal",
    "note_is_in_the_message",
    "order_runs_as_the_person_said_it",
    "reminder_call_shape",
]

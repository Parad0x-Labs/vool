from __future__ import annotations

import re
from typing import Any

from core.execution.models import ToolIntentExecution, _tool_observation
from core.local_operator_actions import OperatorActionIntent, dispatch_operator_action
from core.operator.models import operator_step_executed
from core.tool_argument_aliases import side_effect_class_for_intent


def execute_operator_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    session_id: str,
    dispatch_operator_action_fn=dispatch_operator_action,
    source_context: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    operator_kind = intent.split(".", 1)[1]
    operator_intent = build_operator_action_intent(operator_kind, arguments)
    # Remote-turn fence (core.request_trust.remote_turn_tool_refusal): the local operator lane acts
    # on this computer (processes, disk, files, notes, calendar, reminders), so a turn the server
    # stamped as not the owner's own session is refused here, before anything runs.
    from core.request_trust import remote_turn_refusal_text, remote_turn_tool_refusal

    _remote_refusal = remote_turn_tool_refusal(f"operator.{operator_kind}", source_context)
    if _remote_refusal:
        from core.operator.models import OperatorActionResult

        dispatch = OperatorActionResult(
            ok=False,
            status=_remote_refusal,
            response_text=remote_turn_refusal_text(f"operator.{operator_kind}", source_context),
            details={"kind": operator_kind, "executed": False, "reason": _remote_refusal},
        )
    else:
        dispatch = dispatch_operator_action_fn(
            operator_intent,
            task_id=task_id,
            session_id=session_id,
        )
    # The turn-keyed execution TRUTH: an operator action that ran is a tool that ran, so
    # the signed honesty ledger and the completion-claim guards must read it from the one
    # store built for that question. Without this fact a mixed-demand turn whose
    # note/calendar unit really executed still answers with the honest-looking "cannot
    # verify" replacement, because no other path records the operator lane's execution
    # against the turn. Best-effort, never raises, and no fact without a real turn
    # identity (resolve_turn_key refuses to invent one).
    try:
        from core.execution_truth import KIND_TOOL, record_execution, resolve_turn_key

        turn_key = resolve_turn_key(dict(source_context or {}), None)
        if turn_key:
            record_execution(
                session_id=str(session_id or ""),
                turn_key=turn_key,
                kind=KIND_TOOL,
                name=str(intent or ""),
                ok=operator_step_executed(dispatch),
                status=str(dispatch.status or ""),
                dedupe=f"{intent}:{dispatch.status}",
            )
    except Exception:
        pass
    if dispatch.status == "reported" and side_effect_class_for_intent(intent) == "read_only":
        # A completed catalog/inspection report is evidence, not a proposed effect.
        # Calling it a preview creates a pending approval with nothing to approve.
        mode = "tool_executed" if dispatch.ok else "tool_failed"
    elif dispatch.status == "executed":
        mode = "tool_executed"
    elif dispatch.status in {"reported", "approval_required"}:
        mode = "tool_preview"
    else:
        mode = "tool_failed"
    return ToolIntentExecution(
        handled=True,
        ok=bool(dispatch.ok),
        status=str(dispatch.status),
        response_text=str(dispatch.response_text or ""),
        mode=mode,
        tool_name=intent,
        details={
            **dict(dispatch.details or {}),
            "observation": _tool_observation(
                intent=intent,
                tool_surface="local_operator",
                ok=bool(dispatch.ok),
                status=str(dispatch.status),
                details=dict(dispatch.details or {}),
                response_preview=str(dispatch.response_text or "")[:280],
            ),
        },
        learned_plan=dispatch.learned_plan,
    )


#: Operator tools whose contract input is ``{"text": "the request as written"}``.
_REQUEST_TEXT_KINDS = frozenset(
    {"check_availability", "propose_calendar_event", "update_calendar_event", "save_note", "schedule_reminder"}
)

#: The reminder id inside a cancel request, read the way the chat parser reads it
#: (core.operator.parser's reminder_id_match): a short UI id or a full uuid after "reminder".
_REMINDER_ID_IN_TEXT_RE = re.compile(
    r"\breminders?\s+(?:id\s+)?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8})\b",
    re.IGNORECASE,
)


def _argument(arguments: dict[str, Any], name: str) -> str:
    return str(arguments.get(name) or "").strip()


#: The longest wait a reminder call may give as an amount of time from now: a year, in minutes.
_LONGEST_WAIT_MINUTES = 366 * 24 * 60


def _due_after_a_wait(arguments: dict[str, Any]) -> str | None:
    """The due time of a reminder the call gave as minutes from now ("in 15 minutes", "za 15 minut", "dans 2 heures"),
    worked out on the runtime's clock as a UTC time with its offset; None when the call gave none, or not a whole
    number of minutes in range.

    A model adding minutes to the clock gets it wrong: iMac gate27 on 601705f2c (qwen3:8b), "przypomnij mi za 15 minut,
    żeby zadzwonić do mamy" sent at 04:09:10 was given due_iso 04:09, a time already past by the approval. The runtime
    works the day out from a named weekday the same way (`day`)."""
    raw = arguments.get("in_minutes")
    if raw is None or isinstance(raw, bool) or str(raw).strip() == "":
        return None
    try:
        minutes = float(str(raw).strip())
    except ValueError:
        return None
    if minutes != int(minutes) or not 1 <= minutes <= _LONGEST_WAIT_MINUTES:
        return None
    import contextlib
    from datetime import timedelta, timezone
    from zoneinfo import ZoneInfo

    from core.time_authority import CLOCK
    from core.user_preferences import owner_timezone

    due = (CLOCK.now_utc().astimezone(timezone.utc) + timedelta(minutes=int(minutes))).replace(second=0, microsecond=0)
    # The owner's own wall clock, read in their zone like any time they type, so the reminder is stored and shown in it:
    # a UTC instant was kept as zone "UTC+00:00" and shown in UTC to a +02:00 owner (iMac gate47 on 6ce598256).
    with contextlib.suppress(Exception):
        return due.astimezone(ZoneInfo(owner_timezone())).strftime("%Y-%m-%dT%H:%M")
    return due.strftime("%Y-%m-%dT%H:%M+00:00")


_TYPED_REMINDER_ID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8}", re.IGNORECASE)


def _typed_reminder_id(arguments: dict[str, Any]) -> str:
    """The reminder id a tool call typed, as the listing shows it (8 characters) or whole; "" for anything else."""
    match = _TYPED_REMINDER_ID_RE.fullmatch(_argument(arguments, "reminder_id").strip("()[]#"))
    return match.group(0).lower() if match else ""


def _notes_scope_words(arguments: dict[str, Any]) -> str:
    """' in the "Work" folder of the "iCloud" account': the scope the Apple Notes handlers read back from the request.

    The values go inside quotes on one line, so their own quote marks are dropped and a line break becomes a space
    (a newline in a value read as no folder); a scope only narrows a read."""
    folder, account = (
        re.sub(r"\s*[\r\n\t\v\f]+\s*", " ", re.sub(r'["\u201c\u201d]', "", _argument(arguments, name))).strip()
        for name in ("folder", "account")
    )
    words = f' in the "{folder}" folder' if folder else ""
    if account:
        words += f' of the "{account}" account' if folder else f' in the "{account}" account'
    return words


def build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    target_path = str(arguments.get("target_path") or arguments.get("path") or "").strip() or None
    destination_path = str(arguments.get("destination_path") or arguments.get("destination_dir") or "").strip() or None
    target_label: str | None = None
    reminder_action_id: str | None = None
    reminder_note: str | None = None
    due_iso: str | None = None
    repeat_every: str | None = None
    due_weekday: str | None = None
    note_content: str | None = None
    note_title: str | None = None
    raw_text = ""
    if operator_kind == "move_path":
        source = str(arguments.get("source_path") or target_path or "").strip()
        dest = str(destination_path or "").strip()
        raw_text = f'move "{source}" to "{dest}"'.strip()
        target_path = source or None
    elif operator_kind == "schedule_calendar_event":
        title = str(arguments.get("title") or "VOOL Meeting").strip()
        start_iso = str(arguments.get("start_iso") or "").strip()
        named_day = _argument(arguments, "day")
        if start_iso:
            from core.operator.handlers import _owner_today
            from core.operator.when import align_to_weekday, typed_weekday, weekday_named_in

            weekday = typed_weekday(named_day) if named_day else weekday_named_in(_argument(arguments, "text"))
            if weekday is not None:
                start_iso = align_to_weekday(start_iso, weekday, _owner_today())
        duration_minutes = max(15, int(arguments.get("duration_minutes") or 30))
        raw_text = f'schedule a meeting "{title}" on {start_iso} for {duration_minutes}m'.strip()
    elif operator_kind in {"cancel_reminder", "move_reminder"}:
        # The typed reminder id first (the id the listing shows), else one in the request as written: the
        # handler resolves exactly what the chat lane resolves — that id, the reminder the words name, or the
        # one pending reminder; with several pending and nothing naming one it refuses rather than guess.
        raw_text = str(arguments.get("text") or "").strip()
        id_match = _REMINDER_ID_IN_TEXT_RE.search(raw_text)
        # Only an id-shaped value is an id. A model that was never shown the id types the reminder's own words
        # into reminder_id ("Water the tomatoes"; Mac gate on 12efa4e5, "actually make it 7pm" after the reminder
        # was set in the same chat): read as an id it matched nothing and the move was refused. Those words name
        # the reminder (reminder_note), and words that name no reminder of this chat are refused just the same,
        # never moved onto "the one pending reminder", which may be another one entirely.
        typed_id = _typed_reminder_id(arguments)
        typed_words = _argument(arguments, "note") or ("" if typed_id else _argument(arguments, "reminder_id"))
        reminder_action_id = typed_id or (id_match.group(1).lower() if id_match else "") or None
        reminder_note = " ".join(typed_words.split()) or None
        if operator_kind == "move_reminder":
            due_iso = _argument(arguments, "due_iso") or None
            due_weekday = _argument(arguments, "day") or None
    elif operator_kind in _REQUEST_TEXT_KINDS:
        # These contracts take the request as written (``text``) and their handlers read it the
        # way they read a chat request. Approval is never read from it: approval_requested stays
        # False below, so a proposal or update can only stage, never apply.
        raw_text = str(arguments.get("text") or "").strip()
        if operator_kind == "schedule_reminder":
            reminder_note = _argument(arguments, "note") or None
            due_iso = _argument(arguments, "due_iso") or None
            repeat_every = _argument(arguments, "repeat").casefold() or None
            due_weekday = _argument(arguments, "day") or None
            after = _due_after_a_wait(arguments)
            if after:
                due_iso, due_weekday = after, None
        elif operator_kind == "save_note":
            note_content = str(arguments.get("content") or "").strip() or None
            note_title = _argument(arguments, "title") or None
    elif operator_kind in {"find_notes", "show_note"}:
        # A notes search reads its query, and a note read its title, from the label the chat
        # parser sets (core.operator.notes.find_notes_query, handle_show_note).
        target_label = _argument(arguments, "query" if operator_kind == "find_notes" else "title") or None
    elif operator_kind == "show_agenda":
        # The agenda handler reads its window from the request's words; the window is those words.
        raw_text = _argument(arguments, "window")
    elif operator_kind == "apple_note_list":
        raw_text = "list my Apple notes" + _notes_scope_words(arguments)
    elif operator_kind == "apple_note_read":
        # The title is the label; the folder or account narrows which note of that title is read.
        target_label = _argument(arguments, "title") or None
        raw_text = "read my Apple note" + _notes_scope_words(arguments)
    elif operator_kind == "cleanup_temp_files" and target_path:
        raw_text = f'clean temp files in "{target_path}"'
    elif operator_kind == "inspect_disk_usage" and target_path:
        raw_text = f'find disk bloat in "{target_path}"'
    return OperatorActionIntent(
        kind=operator_kind,
        target_path=target_path,
        destination_path=destination_path,
        approval_requested=False,
        action_id=reminder_action_id or (str(arguments.get("action_id") or "").strip() or None),
        raw_text=raw_text,
        target_label=target_label,
        reminder_note=reminder_note,
        due_iso=due_iso,
        repeat_every=repeat_every,
        due_weekday=due_weekday,
        note_content=note_content,
        note_title=note_title,
    )

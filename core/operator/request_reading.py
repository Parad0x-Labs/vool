"""What the operator lane reads from a request, and which fragments of the request that reading depends on.

MEASURED 2026-09-15 (in-process through the production catalog, on 1d58a641). The operator binder in
`core.agent_runtime.demand_ownership` bound EVERY minted unit whenever `parse_operator_action_intent` claimed the
whole text. 'open my Apple note "Plan", Ukraine situation', 'Ukraine situation, open my Apple note "Plan"' and 'draft
a reply to that, open my Apple note "Plan"' each became ONE execution unit read only by operator_action_dispatch, so
the operator lane could end the turn alone while the topic or the request beside the note vanished (R1e). The same
binding is what keeps 'Personal folder, open my Apple note "Plan"' and 'option 1, propose "Project review"' one
request, and rightly: the folder and the offered slot are arguments the lane reads.

THE READING (`request_reading`) is everything the lane takes from a request's text before it acts: the parsed
intent's fields, and the arguments the handler for that kind reads from the raw text with the lane's own readers --
the Notes folder and account, quoted titles, payloads and new names, a note body, the offered slot a follow-up names,
a note's named action, a due time, a window, a duration, a repeat rule, a series scope, an occurrence date. Readers
that consult the clock or the stored time zone run against one fixed instant in UTC: the reading asks what the
request SAYS, never what time it is on this machine.

A FRAGMENT IS READ (`fragments_the_request_reads`) when blanking it -- its characters written over with spaces, every
offset kept -- changes that reading. A topic beside the request, or a clause of its own, leaves the reading as it
was, whatever its words are. No vocabulary lives here: an argument reader joins by joining its kind's row in
`_ARGUMENT_READERS`, and a kind the lane reads through its intent fields alone is named in `INTENT_ONLY_KINDS`.

A reader that raises is not caught. The reading is then unavailable, not empty, and the grain's fail-closed path
(`demand_ownership._mint_unit_service`) keeps the pre-binder behaviour for that turn.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from .models import OperatorActionIntent
from .parser import parse_operator_action_intent

#: The instant and zone every clock- or zone-reading argument reader runs against (see the module docstring).
_READING_INSTANT = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
_READING_ZONE = "UTC"

Reader = Callable[[str, OperatorActionIntent], Any]


def _at_reading_instant() -> datetime:
    return _READING_INSTANT


# ------------------------------------------------------------------------------------------- the lane's readers


def _notes_scope(text: str, intent: OperatorActionIntent) -> Any:
    from .apple_notes import parse_notes_destination

    return parse_notes_destination(text)


def _quoted_note_values(text: str, intent: OperatorActionIntent) -> Any:
    from .apple_notes import quoted_note_values

    return quoted_note_values(text)


def _new_note_title(text: str, intent: OperatorActionIntent) -> Any:
    from .apple_notes import new_note_title

    return new_note_title(text)


def _note_request(text: str, intent: OperatorActionIntent) -> Any:
    from .notes import parse_note_request

    return parse_note_request(text)


def _notes_query(text: str, intent: OperatorActionIntent) -> Any:
    from .notes import find_notes_query

    return find_notes_query(intent)


def _named_action(text: str, intent: OperatorActionIntent) -> Any:
    from .notes import named_action_reference

    return named_action_reference(text)


def _when(text: str, intent: OperatorActionIntent) -> Any:
    from .when import parse_when_expression

    return parse_when_expression(text, now_fn=_at_reading_instant)


def _reminder_note(text: str, intent: OperatorActionIntent) -> Any:
    from .handlers import _reminder_note_from_text

    return _reminder_note_from_text(text)


def _reminder_repeat(text: str, intent: OperatorActionIntent) -> Any:
    from .handlers import _reminder_repeat_from_text

    return _reminder_repeat_from_text(text)


def _offered_slot(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import offered_slot_number

    return offered_slot_number(text)


def _event_proposal(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import parse_event_proposal

    return parse_event_proposal(text)


def _duration(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import parse_duration_minutes

    return parse_duration_minutes(text)


def _recurrence(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import parse_recurrence_rule

    return parse_recurrence_rule(text)


def _attendees(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import parse_attendees

    return parse_attendees(text)


def _availability_window(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import parse_availability_window

    return parse_availability_window(text, now_fn=_at_reading_instant, zone_name=_READING_ZONE)


def _agenda_window(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_agenda import parse_agenda_window

    return parse_agenda_window(text, now_fn=_at_reading_instant, zone_name=_READING_ZONE)


def _series_scope(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import _names_series_scope

    return _names_series_scope(text)


def _occurrence_date(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import named_occurrence_date

    return named_occurrence_date(text)


def _day_or_clock(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import _has_day_or_clock

    return _has_day_or_clock(text)


def _rename_to(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar_provider import _parse_rename

    return _parse_rename(text)


def _calendar_draft(text: str, intent: OperatorActionIntent) -> Any:
    from .calendar import parse_calendar_request
    from .parser import _extract_quoted_values

    return parse_calendar_request(
        text, extract_quoted_values_fn=_extract_quoted_values, data_path_fn=str, now_fn=_at_reading_instant
    )


def _move_request(text: str, intent: OperatorActionIntent) -> Any:
    from .parser import _extract_quoted_values
    from .storage import parse_move_request

    return parse_move_request(
        text, fallback_source=intent.target_path, fallback_destination=intent.destination_path,
        extract_quoted_values_fn=_extract_quoted_values, data_path_fn=str, expandvars_fn=str,
    )


def _process_sort(text: str, intent: OperatorActionIntent) -> Any:
    from core.execution.constants import machine_process_sort

    return machine_process_sort(text)


#: kind -> the readers its handler applies to the request's raw text (`core.local_operator_actions` dispatch). The
#: calendar mutations list the draft path's readers and the provider path's together: which path runs is decided
#: by state, after the request is read.
_PROVIDER_UPDATE: tuple[Reader, ...] = (_when, _day_or_clock, _rename_to, _series_scope, _occurrence_date)
_ARGUMENT_READERS: dict[str, tuple[Reader, ...]] = {
    "inspect_processes": (_process_sort,),
    "move_path": (_move_request,),
    "schedule_calendar_event": (_calendar_draft,),
    "schedule_reminder": (_when, _reminder_note, _reminder_repeat),
    "move_reminder": (_when,),
    "move_calendar_event": _PROVIDER_UPDATE,
    "update_calendar_event": _PROVIDER_UPDATE,
    "cancel_calendar_event": (_series_scope, _occurrence_date),
    "inspect_calendar_event": (_series_scope, _occurrence_date),
    "check_availability": (_availability_window, _duration),
    "show_agenda": (_agenda_window,),
    "propose_calendar_event": (_offered_slot, _named_action, _event_proposal, _when, _duration, _recurrence, _attendees),
    "save_note": (_note_request, _notes_scope),
    "find_notes": (_notes_query,),
    "apple_note_list": (_notes_scope,),
    "apple_note_read": (_notes_scope,),
    "apple_note_append": (_notes_scope,),
    "apple_note_rename": (_quoted_note_values, _new_note_title, _notes_scope),
    "apple_note_delete": (_notes_scope,),
}

#: Kinds whose handler reads nothing from the raw text beyond the parsed intent's fields.
INTENT_ONLY_KINDS = frozenset(
    {
        "list_tools", "inspect_services", "inspect_disk_usage", "cleanup_temp_files", "list_reminders",
        "cancel_reminder", "complete_reminder", "edit_reminder", "list_calendars", "search_calendar_event",
        "archive_note", "restore_note", "show_note",
    }
)


def read_kinds() -> frozenset[str]:
    """Every kind this reading declares: the kinds with argument readers and the intent-only kinds."""
    return frozenset(_ARGUMENT_READERS) | INTENT_ONLY_KINDS


def request_reading(text: str) -> tuple[Any, ...] | None:
    """What the operator lane takes from `text`: the intent's fields and its kind's arguments, or None when the lane's
    parser claims nothing. Every reader returns a value that compares by content (strings, numbers, tuples, lists,
    dicts, dataclasses such as `WhenResolution`)."""
    intent = parse_operator_action_intent(str(text or ""))
    if intent is None:
        return None
    fields = (intent.kind, intent.target_path, intent.destination_path, intent.approval_requested,
              intent.action_id, intent.target_label)
    arguments = tuple(reader(intent.raw_text, intent) for reader in _ARGUMENT_READERS.get(intent.kind, ()))
    return fields, arguments


def fragments_the_request_reads(text: str, spans: Sequence[tuple[int, int]]) -> tuple[bool, ...]:
    """For each (start, end) span of `text`: whether the operator lane's reading of the whole request depends on it.

    A span is read when the reading of `text` with that span blanked differs from the reading of `text`. When the
    lane's parser claims nothing in `text`, no span is read.
    """
    value = str(text or "")
    whole = request_reading(value)
    if whole is None:
        return tuple(False for _span in spans)
    read: list[bool] = []
    for start, end in spans:
        start, end = max(0, int(start)), min(len(value), int(end))
        blanked = value[:start] + " " * max(0, end - start) + value[end:]
        read.append(request_reading(blanked) != whole)
    return tuple(read)

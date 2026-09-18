from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.reasoning_engine import Plan

#: Operator kinds that only READ -- the machine, a calendar, the notes -- and change nothing. One
#: classification for every reader: the served door refuses every OTHER kind in read-only Ask/Plan
#: mode, and the memory lane hands a storage request naming one of the other kinds to the operator
#: lane instead of keeping it as a fact. The same classification is declared per tool in
#: `core.runtime_tool_contracts` (side_effect_class="read_only"), which the served result stamp
#: reads (`turn_dispatch._reported_read_only`): keep the two in one step.
READ_ONLY_OPERATOR_KINDS = frozenset(
    {"inspect_disk_usage", "inspect_processes", "inspect_services", "list_tools", "list_reminders",
     # Provider-backed calendar reads and note reads: pure reads on the served path too.
     "check_availability", "list_calendars", "inspect_calendar_event", "search_calendar_event",
     "show_agenda", "find_notes", "show_note",
     # Apple Notes reads: a note list or a note body changes nothing (2026-09-15, with the
     # read-report result-truth repair -- a finished read was filed pending_approval because
     # these kinds were missing from every read classification).
     "apple_note_read", "apple_note_list"}
)


@dataclass(frozen=True)
class OperatorActionIntent:
    kind: str
    target_path: str | None = None
    destination_path: str | None = None
    approval_requested: bool = False
    action_id: str | None = None
    raw_text: str = ""
    target_label: str | None = None


@dataclass
class OperatorActionResult:
    ok: bool
    status: str
    response_text: str
    details: dict[str, Any]
    learned_plan: Plan | None = None


def operator_step_executed(result: Any) -> bool:
    """Whether an operator step RAN, for the execution record -- not whether its request succeeded.

    A request can fail after its lane already wrote something. An Apple Notes delivery the OS refused
    still saves the labelled workspace fallback note the answer points to; filing that step as a failed
    tool erased a write that happened, and the final claim guard then replaced the whole answer -- the
    fallback's path and the retry guidance with it -- by "I cannot verify the claimed action from this
    turn's execution record" (measured on the served path, revision-5 served journeys, second run). A
    lane declares such a write with ``details["files_modified"] = True``; the request's own outcome
    still travels in ``ok`` and ``status``.
    """
    if bool(getattr(result, "ok", False)):
        return True
    details = getattr(result, "details", None)
    return isinstance(details, dict) and details.get("files_modified") is True

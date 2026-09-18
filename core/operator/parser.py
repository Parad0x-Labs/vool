from __future__ import annotations

import re

from core.execution.constants import asks_runtime_for_a_fact, text_without_paths

from .models import OperatorActionIntent

# What a storage scan has to be ABOUT before it can be the answer. See the gate at its use site.
_STORAGE_TOPIC_RE = re.compile(
    r"\b(?:disk|disks|drive|drives|ssd|hdd|volume|volumes|partition|partitions|storage|space|"
    r"capacity|folder|folders|file|files|bloat|temp|cache)\b",
    re.IGNORECASE,
)

_INSPECT_HINTS = (
    "disk bloat",
    "what is eating space",
    "what's eating space",
    "taking space",
    "space usage",
    "find large files",
    "find the large files",
    "biggest files",
    "largest files",
    "find c drive",
    "find disk",
)
_CLEAN_HINTS = ("clean temp", "cleanup temp", "clean cache", "cleanup cache", "remove temp")
_PROCESS_HINTS = (
    "what processes",
    "top processes",
    "startup offenders",
    "process offenders",
    "memory hogs",
    "cpu hogs",
)
_SERVICE_HINTS = (
    "what services",
    "running services",
    "service offenders",
    "startup services",
    "startup items",
    "launch agents",
)
_TOOL_HINTS = (
    "what tools do you have",
    "list tools",
    "show tools",
    "tool inventory",
    "what can you execute",
    "what actions can you take",
)
_SCHEDULE_HINTS = (
    "schedule a meeting",
    "schedule meeting",
    "create calendar event",
    "create meeting",
    "schedule an event",
)
_REMINDER_HINT_RE = re.compile(r"\bremind\s+me\b|\b(?:set|create|schedule|add)\s+(?:(?:a|an|the|my)\s+)?reminder\b", re.IGNORECASE)
_LIST_REMINDERS_RE = re.compile(
    r"\b(?:what|which|show|list)\b[^\n!?.]*\breminders?\b|\bmy\s+reminders?\b", re.IGNORECASE
)
_CANCEL_REMINDER_RE = re.compile(
    r"\b(?:cancel|delete|remove|call\s+off|scrub)\b[^\n!?.]*\breminders?\b", re.IGNORECASE,
)
_MOVE_REMINDER_RE = re.compile(
    r"\b(?:move|push|shift|delay|postpone|reschedule|resched)\b[^\n!?.!]*\breminders?\b", re.IGNORECASE,
)
_COMPLETE_REMINDER_RE = re.compile(
    r"\b(?:complete|completed|finish(?:ed)?|check(?:ed)? off|mark)\b[^\n!?.]*\breminders?\b"
    r"|\breminders?\b[^\n!?.]*\b(?:is|as)?\s*done\b",
    re.IGNORECASE,
)
_EDIT_REMINDER_RE = re.compile(
    r"\b(?:edit|rename|reword|change)\b[^\n!?.]*\breminders?\b", re.IGNORECASE,
)
_CANCEL_CALENDAR_RE = re.compile(
    r"\b(?:cancel|delete|remove|call\s+off|scrub)\b[^\n!?.]*\b(?:calendar|meeting|event|draft|\.ics|ics)\b", re.IGNORECASE
)
_MOVE_CALENDAR_RE = re.compile(
    r"\b(?:move|push|shift|reschedule|resched|change)\b[^\n!?.!]*\b(?:calendar|meeting|event|series|draft)\b", re.IGNORECASE
)
# The provider-backed availability lane: the request asks what is free/busy on a day or
# calendar. Checked with the draft-calendar kinds above so "cancel the event" still wins,
# but before the storage/process families and before _SCHEDULE_HINTS.
_FREE_SLOT_RE = re.compile(
    r"\bfree[\s-]*\d*[\s-]*(?:minute|min|hour|hr|half)?[\s-]*slot", re.IGNORECASE
)
_AVAILABILITY_RE = re.compile(
    r"\b(?:availability|available|free|busy|open)\b[^\n!?.]*\b(?:slot|time|calendar|schedule|window)\b"
    r"|\b(?:slot|calendar|schedule)\b[^\n!?.]*\b(?:availability|available|free|busy)\b"
    r"|\b(?:check|show|what|find|look)\b[^\n!?.]*\b(?:my|the)\s+(?:calendar|schedule)\b",
    re.IGNORECASE,
)
# The agenda lane: the request asks WHAT IS ON the chosen calendars on a day/range. It must
# be checked before _AVAILABILITY_RE, whose broad "(show|what) ... my calendar" would otherwise
# claim every agenda question for the free-slot lane.
_AGENDA_RE = re.compile(
    r"\bwhat(?:'s| is)\s+on\s+(?:my|the|their|our)\s+(?:calendar|schedule|plate)\b"
    r"|\bwhat\s+do\s+(?:i|you|we)\s+have\b[^\n!?.]*\b(?:today|tomorrow|this week|next week|upcoming)\b"
    r"|\b(?:show|read|walk (?:me )?through)\b[^\n!?.]*\b(?:my|the)\s+(?:day|agenda)\b"
    r"|\b(?:my|the|this)\s+agenda\b[^\n!?.]*\b(?:for|from|this week|next week|today|tomorrow)\b"
    r"|\bagenda\s+(?:for|from)\b"
    r"|\bupcoming\s+(?:events?|meetings?|appointments?)\b"
    r"|\b(?:on|for)\s+(?:my|the)\s+calendar\b[^\n!?.]*\b(?:this week|next week|today|tomorrow)\b"
    r"|\bcalendar\b[^\n!?.]*\bfrom\s+[0-9]{4}-[0-9]{2}-[0-9]{2}\b",
    re.IGNORECASE,
)
_SEARCH_CALENDAR_RE = re.compile(
    r"\b(?:search|find|look (?:up|for))\b[^\n!?.]*\bcalendar\b[^\n!?.]*\b(?:for|about|named|called|containing)\b"
    r"|\b(?:search|find)\b[^\n!?.]*\b(?:events?|meetings?|appointments?)\b[^\n!?.]*\b(?:about|named|called|containing|with)\b",
    re.IGNORECASE,
)
_LIST_CALENDARS_RE = re.compile(
    r"\b(?:list|show)\s+(?:my\s+|the\s+|all\s+|me\s+my\s+)?calendars?\b"
    r"|\b(?:which|what)\s+calendars?\b",
    re.IGNORECASE,
)
_INSPECT_EVENT_RE = re.compile(
    r"\b(?:show|describe|inspect|view|check|details\s+(?:of|for))\b[^\n!?.]*\bevent\b", re.IGNORECASE
)
_PROPOSE_EVENT_RE = re.compile(
    r'\bpropose\s+(?:a|an|the)?\s*(?:[\w-]+\s+){0,2}?(?:meeting|event|call|review|sync|appointment|slot)\b'
    r'|\bpropose\s+["\u201c]'
    r'|\boption\s+\d+\b[^\n!?.]*\b(?:propose|schedule|book|create)\b'
    r'|\bschedule\s+(?:the\s+)?[^\n!?.]*\baction\b[^\n!?.]*\bnote', re.IGNORECASE
)
_RENAME_EVENT_RE = re.compile(
    r"\b(?:rename|retitle)\b[^\n!?.]*\b(?:event|meeting|calendar|it)\b|\bchange\s+the\s+title\s+of\b", re.IGNORECASE
)
_SAVE_NOTE_RE = re.compile(
    r"\b(?:save|write|store|create|jot|take)\b[^\n!?.]*\bnote\b|\bsave\s+meeting\s+notes\b|\bnote\s+(?:this|that)\s+down\b", re.IGNORECASE
)
_FIND_NOTES_RE = re.compile(
    r"\b(?:find|search|look\s+(?:up|for)|list|show)\b[^\n!?.]*\bnotes?\b", re.IGNORECASE
)
# The recoverable-delete lane for workspace notes, and the Apple Notes family. Both are
# anchored on their own words so the workspace save/show/search lanes above keep their requests.
_ARCHIVE_NOTE_RE = re.compile(
    r"\b(?:archive|delete|remove)\b[^\n!?.]*\bnotes?\b", re.IGNORECASE,
)
_RESTORE_NOTE_RE = re.compile(
    r"\b(?:restore|bring back|unarchive)\b[^\n!?.]*\bnotes?\b", re.IGNORECASE,
)
_APPLE_NOTE_LIST_RE = re.compile(r"\b(?:list|show)\b[^\n!?.]*\bapple\s+notes\b", re.IGNORECASE)
_APPLE_NOTE_READ_RE = re.compile(r"\b(?:read|show|open|display)\b[^\n!?.]*\bapple\s+note\b", re.IGNORECASE)
_APPLE_NOTE_APPEND_RE = re.compile(r"\bappend\b[^\n!?.]*\bapple\s+note\b", re.IGNORECASE)
_APPLE_NOTE_RENAME_RE = re.compile(r"\brename\b[^\n!?.]*\bapple\s+note\b", re.IGNORECASE)
_APPLE_NOTE_DELETE_RE = re.compile(r"\b(?:delete|remove)\b[^\n!?.]*\bapple\s+note\b", re.IGNORECASE)
_SHOW_NOTE_RE = re.compile(
    r"\b(?:show|read|open|display)\s+(?:the|my|that)\s+note\b"
    r"|\bwhat(?:'s| is)(?:\s+in)?\s+(?:the|my|that)\s+note\b"
    r"|\b(?:show|read|open|display)\b[^\n!?.]*\bnote\s+(?:titled|called|named)\b",
    re.IGNORECASE,
)
# Words that approve the action a request names. One counts only as a word of the request itself: whole, and outside every
# value the request names (see `_approval_requested`). Exact on purpose: an approval is never typo-folded.
_APPROVAL_HINTS = ("approve", "go ahead", "do it", "proceed", "yes", "fuck it", "clean all", "delete all", "remove all")
_APPROVAL_WORDS_RE = re.compile(
    r"\b(?:" + "|".join(r"\s+".join(map(re.escape, hint.split())) for hint in _APPROVAL_HINTS) + r")\b", re.IGNORECASE
)
_APPROVE_WORD_RE = re.compile(r"\bapprove\b", re.IGNORECASE)
_APPROVAL_ID_RE = re.compile(
    r"\b(?:approve|cleanup|clean|schedule|calendar|meeting|move|archive)\s+([0-9a-f]{8}-[0-9a-f-]{27,})\b",
    re.IGNORECASE,
)
_MOVE_WORD_RE = re.compile(r"\b(?:move|relocate|archive)\b", re.IGNORECASE)
_QUOTED_PATH_RE = re.compile(r"""["']([^"']+)["']""")
_WINDOWS_PATH_RE = re.compile(r"\b([A-Za-z]:\\[^\n\r\"']*)")
_POSIX_PATH_RE = re.compile(r"\b(?:in|on|under|at)\s+(/[^?\n\r]+)")
_SPACE_WORD_RE = re.compile(r"\bspace\b")
_DELETE_TEMP_FILES_RE = re.compile(r"\bdelete\s+temp\s+files?\b", re.IGNORECASE)


def _head_words_re(*phrases: str) -> re.Pattern[str]:
    """The first word of each phrase, whole: the words that name the action a recognizer's phrases ask for."""
    heads = sorted({phrase.split()[0] for phrase in phrases})
    return re.compile(r"\b(?:" + "|".join(map(re.escape, heads)) + r")\b", re.IGNORECASE)


# The words that name the action an approving kind runs, taken from the kind's own recognizer. A negation that reaches one
# withholds the approval ('yes, but do not remove temp files'); see `core.operator.approval_polarity`.
_SCHEDULE_ACTION_RE = _head_words_re(*_SCHEDULE_HINTS)
_CLEANUP_ACTION_RE = _head_words_re(*_CLEAN_HINTS, "clean temp files", "delete temp files")
# The Notes delete and calendar proposal recognizers also need the words around their verb ("apple note", "meeting"), which
# an action taken back with a pronoun does not repeat ("actually, don't delete it"). Their verbs name the action.
_NOTE_DELETE_ACTION_RE = _head_words_re("delete", "remove")
_PROPOSE_ACTION_RE = _head_words_re("propose", "book", "schedule", "create")


def parse_operator_action_intent(user_text: str) -> OperatorActionIntent | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    # Recognition reads the request with its URLs and filesystem paths blanked: a path NAMES a place,
    # and its components are never words of the request (see `text_without_paths`). Every extractor
    # below still reads `text`, so a path argument arrives unchanged.
    words = text_without_paths(text)
    lowered = words.lower()
    quoted_values = _extract_quoted_values(text)
    target_path = quoted_values[0] if quoted_values else _extract_path(text)
    destination_path = quoted_values[1] if len(quoted_values) >= 2 else None
    action_id = _extract_action_id(text)

    if any(hint in lowered for hint in _TOOL_HINTS):
        return OperatorActionIntent(kind="list_tools", raw_text=text)

    # --- Scheduling vertical intents, checked BEFORE the storage/process families so a
    # "remind me to clean temp files tomorrow" is a reminder, not a cleanup; and before
    # _SCHEDULE_HINTS so "reschedule a meeting" is a MOVE, not a new draft ("schedule a
    # meeting" is a substring of "reschedule a meeting" and would otherwise win).
    if _REMINDER_HINT_RE.search(words):
        return OperatorActionIntent(kind="schedule_reminder", raw_text=text)
    reminder_id_match = re.search(
        r"\breminder\s+(?:id\s+)?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8})\b",
        text, re.IGNORECASE,
    )
    reminder_id = reminder_id_match.group(1).lower() if reminder_id_match else None
    if _CANCEL_REMINDER_RE.search(words):
        return OperatorActionIntent(kind="cancel_reminder", action_id=reminder_id, raw_text=text)
    if _MOVE_REMINDER_RE.search(words):
        return OperatorActionIntent(kind="move_reminder", action_id=reminder_id, raw_text=text)
    if _COMPLETE_REMINDER_RE.search(words):
        return OperatorActionIntent(kind="complete_reminder", action_id=reminder_id, raw_text=text)
    if _EDIT_REMINDER_RE.search(words):
        return OperatorActionIntent(kind="edit_reminder", action_id=reminder_id, target_label=_reminder_edit_text(text), raw_text=text)
    if _LIST_REMINDERS_RE.search(words):
        return OperatorActionIntent(kind="list_reminders", raw_text=text)
    if _CANCEL_CALENDAR_RE.search(words):
        calendar_id, title = _calendar_target(text)
        return OperatorActionIntent(kind="cancel_calendar_event", action_id=calendar_id, target_label=title, raw_text=text)
    if _MOVE_CALENDAR_RE.search(words):
        calendar_id, title = _calendar_target(text)
        return OperatorActionIntent(kind="move_calendar_event", action_id=calendar_id, target_label=title, raw_text=text)

    # --- Provider-backed calendar reads and proposals. Availability first: "check
    # Tuesday afternoon for a free slot" is a read, never a mutation. Notes intents sit
    # with the scheduling family so "remind me to take notes" is still a reminder (the
    # reminder regex above already claimed it).
    if _AGENDA_RE.search(words):
        return OperatorActionIntent(kind="show_agenda", raw_text=text)
    if _SEARCH_CALENDAR_RE.search(words):
        return OperatorActionIntent(kind="search_calendar_event", target_label=_event_label(text) or _calendar_name(text), raw_text=text)
    if _FREE_SLOT_RE.search(words) or _AVAILABILITY_RE.search(words):
        return OperatorActionIntent(kind="check_availability", target_label=_calendar_name(text), raw_text=text)
    if _LIST_CALENDARS_RE.search(words):
        return OperatorActionIntent(kind="list_calendars", raw_text=text)
    if _INSPECT_EVENT_RE.search(words):
        return OperatorActionIntent(
            kind="inspect_calendar_event",
            target_label=_event_label(text),
            destination_path=_calendar_name(text),
            raw_text=text,
        )
    if _APPLE_NOTE_LIST_RE.search(words):
        return OperatorActionIntent(kind="apple_note_list", raw_text=text)
    if _APPLE_NOTE_READ_RE.search(words):
        return OperatorActionIntent(kind="apple_note_read", target_label=_apple_note_title(text), raw_text=text)
    if _APPLE_NOTE_APPEND_RE.search(words):
        return OperatorActionIntent(kind="apple_note_append", target_label=_apple_note_title(text),
                                    destination_path=_apple_note_append_text(text), raw_text=text)
    if _APPLE_NOTE_RENAME_RE.search(words):
        return OperatorActionIntent(kind="apple_note_rename", target_label=_apple_note_title(text),
                                    destination_path=_apple_note_title(text), raw_text=text)
    if _APPLE_NOTE_DELETE_RE.search(words):
        title = _apple_note_title(text)
        return OperatorActionIntent(kind="apple_note_delete", target_label=title,
                                    approval_requested=_approval_requested(text, data=_unquoted_values(text, title),
                                                                           action=_NOTE_DELETE_ACTION_RE),
                                    raw_text=text)
    if _RESTORE_NOTE_RE.search(words):
        return OperatorActionIntent(kind="restore_note", target_label=_note_title(text), raw_text=text)
    if _ARCHIVE_NOTE_RE.search(words):
        return OperatorActionIntent(kind="archive_note", target_label=_note_title(text), raw_text=text)
    if _SAVE_NOTE_RE.search(words):
        return OperatorActionIntent(kind="save_note", raw_text=text)
    if _SHOW_NOTE_RE.search(words):
        # "show the note X" (singular, titled) before the plural search lane: "show my
        # notes" stays a search because the singular \bnote\b does not match "notes".
        return OperatorActionIntent(kind="show_note", target_label=_note_title(text), raw_text=text)
    if _FIND_NOTES_RE.search(words):
        return OperatorActionIntent(kind="find_notes", target_label=_note_query(text), raw_text=text)
    if _PROPOSE_EVENT_RE.search(words):
        return OperatorActionIntent(
            kind="propose_calendar_event",
            approval_requested=_approval_requested(text, data=_time_values(text), action=_PROPOSE_ACTION_RE),
            action_id=action_id,
            target_label=_calendar_name(text),
            raw_text=text,
        )
    if _RENAME_EVENT_RE.search(words):
        calendar_id, title = _calendar_target(text)
        return OperatorActionIntent(kind="update_calendar_event", action_id=calendar_id, target_label=title, raw_text=text)

    if any(hint in lowered for hint in _PROCESS_HINTS):
        return OperatorActionIntent(kind="inspect_processes", raw_text=text)

    if any(hint in lowered for hint in _SERVICE_HINTS):
        return OperatorActionIntent(kind="inspect_services", raw_text=text)

    if any(hint in lowered for hint in _SCHEDULE_HINTS) or (
        action_id and any(token in lowered for token in ("calendar", "meeting", "schedule")) and _says_approve(text)
    ):
        return OperatorActionIntent(
            kind="schedule_calendar_event",
            approval_requested=_approval_requested(text, data=_time_values(text), action=_SCHEDULE_ACTION_RE),
            action_id=action_id,
            raw_text=text,
        )

    if (
        any(hint in lowered for hint in _CLEAN_HINTS)
        or ("temp files" in lowered and "clean" in lowered)
        or _DELETE_TEMP_FILES_RE.search(lowered)
        # The approval the cleanup preview itself offers ('Reply with: approve cleanup <id>',
        # `handlers.handle_cleanup_temp_files`) named no kind here, so the offered confirmation
        # parsed as no operator action -- the same id-shaped arm the move and calendar branches
        # already have, gated the same way on `_says_approve` ("don't approve cleanup <id>"
        # selects no cleanup).
        or (action_id and any(token in lowered for token in ("cleanup", "clean")) and _says_approve(text))
    ):
        return OperatorActionIntent(
            kind="cleanup_temp_files",
            target_path=target_path,
            approval_requested=_approval_requested(text, action=_CLEANUP_ACTION_RE),
            action_id=action_id,
            raw_text=text,
        )

    if (target_path and _MOVE_WORD_RE.search(lowered)) or (
        action_id and any(token in lowered for token in ("move", "archive")) and _says_approve(text)
    ):
        return OperatorActionIntent(
            kind="move_path",
            target_path=target_path,
            destination_path=destination_path,
            approval_requested=_approval_requested(text, action=_MOVE_WORD_RE),
            action_id=action_id,
            raw_text=text,
        )

    if any(hint in lowered for hint in _INSPECT_HINTS) or (
        _SPACE_WORD_RE.search(lowered) and any(re.search(rf"\b{token}\b", lowered) for token in ("disk", "drive", "folder", "storage"))
    ):
        # The co-occurrence of "space" and a storage noun says the sentence MENTIONS storage, not
        # that it asks this machine about its own. Measured on the deployed build 2026-07-30, "My
        # landlord says the storage space in the basement is included in the rent, is that normal
        # in Lithuania?" was answered with a scan of the home directory, a ranking of its largest
        # folders, and an offer to delete temp files. The ask in that sentence is "is that normal
        # in Lithuania" -- storage is not in it at all. The gate reads the same path-blind words as every
        # recognizer above: given the original text, a directory named `.../review/02-calendar-notes/...`
        # read as a request to review notes, and a disk request became advice.
        if asks_runtime_for_a_fact(words, _STORAGE_TOPIC_RE):
            return OperatorActionIntent(kind="inspect_disk_usage", target_path=target_path, raw_text=text)

    return None


def _extract_path(text: str) -> str | None:
    match = _QUOTED_PATH_RE.search(text)
    if match:
        return match.group(1).strip()
    match = _WINDOWS_PATH_RE.search(text)
    if match:
        return match.group(1).strip().rstrip(".,")
    match = _POSIX_PATH_RE.search(text)
    if match:
        return match.group(1).strip().rstrip(".,")
    return None


def _extract_quoted_values(text: str) -> list[str]:
    return [match.group(1).strip() for match in _QUOTED_PATH_RE.finditer(text or "") if match.group(1).strip()]


def _extract_action_id(text: str) -> str | None:
    match = _APPROVAL_ID_RE.search(text)
    if not match:
        return None
    return match.group(1)


def request_own_words(text: str, *, data: tuple[str, ...] = ()) -> str:
    """The words the user said to the runtime: `text` with every value it names, and every path and URL, blanked out.

    A word inside a note title, a folder or account name, a quoted file name or a path belongs to that value. The values
    come from `core.operator.apple_notes.words_outside_values` (quoted values, folder and account names, and `data`: values
    a reader took from outside quotes); the paths from `text_without_paths`. The approval words below and the proceed
    words of a follow-up turn (`core.agent_runtime.proceed_intent_support`) are read only in these words.
    """
    from core.operator.apple_notes import words_outside_values

    return text_without_paths(words_outside_values(text, data=data))


def _approval_requested(text: str, *, data: tuple[str, ...] = (), action: re.Pattern[str] | None = None) -> bool:
    """True when the request's own words approve the action it names, and nothing they say withholds that approval.

    An approval word counts whole and outside every value. Measured at 4f18cfdb: a substring match over the whole request
    approved 'delete my Apple note "Proceeds 2026"' and read 'is calendar <id> approved?' as the approval of that draft. It
    then stands only when the request does not negate, refuse, doubt, retract or defer it, or the `action` it names.
    Measured at cbe05fa3: 'delete my Apple note "Plan", actually don't do it' and "don't clean all temp files" parsed as
    approved. See `core.operator.approval_polarity`; in doubt, the request is asked about again.
    """
    from core.operator.approval_polarity import approval_withheld

    words = request_own_words(text, data=data)
    return _APPROVAL_WORDS_RE.search(words) is not None and not approval_withheld(
        words, approvals=_APPROVAL_WORDS_RE, actions=action)


def _says_approve(text: str) -> bool:
    """The request's own words say "approve", the word an approval id is approved with ('approve move <id>'), and nothing
    they say withholds it. "don't approve move <id>" selects no approval kind, so the kind alone runs nothing."""
    from core.operator.approval_polarity import approval_withheld

    words = request_own_words(text)
    return _APPROVE_WORD_RE.search(words) is not None and not approval_withheld(
        words, approvals=_APPROVE_WORD_RE, actions=_APPROVAL_ID_RE)


def _time_values(text: str) -> tuple[str, ...]:
    """The times a calendar request names ('tomorrow at 10am', '2026-09-21 15:30'). They are the event's time, a value the
    calendar kinds take, and never a deferral of the approval."""
    from core.operator.when import time_expression_spans

    return tuple(text[start:end] for start, end in time_expression_spans(text))


def _unquoted_values(text: str, *values: str | None) -> tuple[str, ...]:
    """The values a reader took from outside quotes ('titled Go ahead list'): data to blank before reading approval."""
    from core.operator.apple_notes import quoted_note_values

    quoted = quoted_note_values(text)
    return tuple(value for value in values if value and value not in quoted)


def unquoted_request_values(text: str) -> tuple[str, ...]:
    """The values the request names that the parser read from outside quotes: a title after 'titled', 'called' or 'named',
    a payload after 'with', a new name. A word inside one belongs to that value, exactly as a word inside a quoted title.

    The Notes delete branch passes its own title as `data` when it reads approval. A reader that does not know the
    request's kind -- the follow-up proceed reading in `core.agent_runtime.proceed_intent_support` -- asks here for the
    values of whatever request the text names. Measured at cbe05fa3: after a delete question, 'delete my apple note titled
    Go ahead list' read "go ahead" out of the title, resumed the question and dropped the new request.
    """
    intent = parse_operator_action_intent(text)
    if intent is None:
        return ()
    return _unquoted_values(text, intent.target_label, intent.destination_path)


def _append_text(text: str) -> str | None:
    """The text to append to an Apple note: quoted, or what follows 'with'/'saying'."""
    quoted = _extract_quoted_values(str(text or ""))
    if len(quoted) >= 2:
        return quoted[1]
    if quoted:
        return quoted[0]
    match = re.search(r"\b(?:with|saying|:\\s*)\s+(.+)$", str(text or ""), re.IGNORECASE)
    if match:
        return match.group(1).strip(" .!?") or None
    return None


def _apple_note_title(text: str) -> str | None:
    """An Apple note's title: its first quoted value that is data, or 'note titled/called/named/about X'.

    A quoted folder or account name ('in the "Work" folder') is scope, never the title; see
    `core.operator.apple_notes.quoted_note_values`.
    """
    from core.operator.apple_notes import quoted_note_values

    values = quoted_note_values(text)
    if values:
        return values[0]
    match = re.search(r"\bnotes?\s+(?:titled|called|named|about)\s+([^\n,.]+)", str(text or ""), re.IGNORECASE)
    return (match.group(1).strip() or None) if match else None


def _apple_note_append_text(text: str) -> str | None:
    """The text to append to an Apple note: its second quoted data value, or what follows 'with'/'saying'.

    One quoted value is the title, never also the text to append: 'append to my apple note "Groceries" with oat milk'
    appends "oat milk", and with no text after 'with' there is nothing to append.
    """
    from core.operator.apple_notes import quoted_note_values

    values = quoted_note_values(text)
    if len(values) >= 2:
        return values[1]
    match = re.search(r"\b(?:with|saying|:\\s*)\s+(.+)$", str(text or ""), re.IGNORECASE)
    if match:
        return match.group(1).strip(" .!?\"'") or None
    return None


def _reminder_edit_text(text: str) -> str | None:
    """The new wording for a reminder edit: quoted, or what follows 'to say'."""
    quoted = _extract_quoted_values(str(text or ""))
    if quoted:
        return quoted[0]
    match = re.search(r"\breminders?\b[^\n!?.]*?\bto say\s+([^.\n!?]+)", str(text or ""), re.IGNORECASE)
    if match:
        return match.group(1).strip(" .!?") or None
    return None


def _calendar_target(text: str) -> tuple[str | None, str | None]:
    head = re.split(r"\s+(?:to|on|at)\s+", text, maxsplit=1, flags=re.IGNORECASE)[0]
    identity = re.search(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{8}\b", head, re.IGNORECASE)
    quoted = _extract_quoted_values(head)
    if identity:
        return identity.group(0).lower(), quoted[0] if quoted else None
    if quoted:
        return None, quoted[0]
    label = re.sub(r"\b(?:cancel|delete|remove|call\s+off|scrub|move|push|shift|reschedule|resched|change|the|my|a|an|calendar|meeting|event|draft)\b", " ", head, flags=re.IGNORECASE)
    label = " ".join(label.strip(" .!?").split())
    return None, label or None


def _calendar_name(text: str) -> str | None:
    """A calendar the user NAMES ('on the Team calendar', 'on the London calendar')."""
    match = re.search(
        r"\b(?:on|in|from|under)\s+(?:the\s+|my\s+)?([A-Z][\w&\- ]{1,30}?)\s+calendar\b",
        str(text or ""),
    )
    if not match:
        return None
    return match.group(1).strip() or None


def _event_label(text: str) -> str | None:
    """The event a user names: quoted, or the noun phrase before 'event'."""
    quoted = _extract_quoted_values(str(text or ""))
    if quoted:
        return quoted[0]
    match = re.search(
        r"\b(?:event|meeting)\s+(?:called|named|titled)\s+([^\n,.]+)",
        str(text or ""), re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    match = re.search(
        r"\b(?:the|my|an?)\s+(?:project\s+|quick\s+|team\s+|prep\s+|release\s+)?([A-Za-z][\w \-']{2,40}?)\s+(?:event|meeting)\b",
        str(text or ""), re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None


def _note_query(text: str) -> str | None:
    """The search terms after 'notes about/on/for/containing'."""
    match = re.search(
        r"\bnotes?\s+(?:about|on|for|containing|matching|with)\s+(.+)$",
        str(text or ""), re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(" .?!") or None
    return None


def _note_title(text: str) -> str | None:
    """The note title a user asks to display: quoted, or 'note titled/about X'."""
    quoted = _extract_quoted_values(str(text or ""))
    if quoted:
        return quoted[0]
    match = re.search(
        r"\bnotes?\s+(?:titled|called|named|about)\s+([^\n,.]+)",
        str(text or ""), re.IGNORECASE,
    )
    if match:
        return match.group(1).strip() or None
    return None

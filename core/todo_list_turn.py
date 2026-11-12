"""The to-do list VOOL keeps, from chat: add, tick off, take off, show.

The store is core/pa_tasks.py. This module reads one chat turn and either answers it from that
store or returns ``None`` so the turn keeps the lanes it has today.

Two lists can answer "what's on my to-do list": the list VOOL keeps here, and a to-do FILE on the
computer (Desktop/todo.txt and the like, read by ``read_the_named_todo_list`` in
core/agent_runtime/fast_paths_machine.py, or by the clean local loop). The rule, in this order:

1. The ask names a file or a place on the computer ("my to-do file", "todo.txt", "the to-do list
   on my desktop", "in Documents"): the FILE. This module stands down.
2. The ask names VOOL ("my VOOL to-do list", "the to-do list in VOOL", "the list you keep"):
   the VOOL list, even when it is empty.
3. A plain read ("what's on my to-do list"): the VOOL list once the person has ever kept an item in
   it; before that, the FILE (exactly the answer 0.7.x gave). When the VOOL list answers on the
   owner's own computer and a to-do file exists, the reply says so and how to read the file.
4. Adding, ticking off and taking off always mean the VOOL list, unless the ask names a file or a
   place (then the file lanes keep it; they never write the file without the change card).

Writes fail closed: an item that matches more than one entry is never guessed (the person is
asked which), a missing item changes nothing, and in a private chat nothing new is saved. Only
the owner's own turns and a paired phone reach the list; an agent's brief or an unknown remote
stamp keeps the lanes it had.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core import pa_tasks

ROUTE_REASON = "vool_todo_list"

# --------------------------------------------------------------------------------------------
# reading the turn
# --------------------------------------------------------------------------------------------

# The noun. "to do" as two bare words is a verb phrase ("what do I have to do today"), so the
# spaced form counts only with "list" after it. "checklist" stays with the file lanes.
_NOUN = r"(?:to[\-_]?dos?(?:\s+lists?)?|to\s+do\s+lists?)"
_NOUN_RE = re.compile(rf"\b{_NOUN}\b", re.I)
_OWNER = r"(?:my|the|our|your|vool'?s)"
_VOOL_WORD = r"(?:vool\s+)?"
_LIST_REF = rf"(?:{_OWNER}\s+{_VOOL_WORD}{_NOUN}|{_OWNER}\s+{_VOOL_WORD}list)"
_IN_VOOL = r"(?:\s+(?:in|on)\s+vool)?"

# A file or a place on the computer: the file lanes' ask, never this list's.
_FILE_CUE_RE = re.compile(
    r"\b(?:file|files|txt|md|docx?|pdf|notes?\s+app|apple\s+notes|desktop|documents|downloads|"
    r"folder|computer|laptop|mac|disk|drive|icloud|dropbox)\b|\.\w{2,4}\b|[~/\\]",
    re.I,
)
# VOOL named as the keeper of the list.
_VOOL_CUE_RE = re.compile(
    r"\bvool\b|\b(?:list|to-?dos?)\s+(?:that\s+)?you(?:\s+keep|\s+are\s+keeping|'re\s+keeping|\s+have)\b|"
    r"\byour\s+(?:to-?do\s+)?list\b|\bin\s+(?:here|the\s+app)\b",
    re.I,
)

_LEAD_RE = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|and|also|oh|right|vool)\b[\s,!.:-]*|"
    r"(?:please|pls|plz|kindly)\b[\s,]*|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:you\s+)?to\s+)|"
    r"(?:let'?s\s+))+",
    re.I,
)
_TAIL_RE = re.compile(r"(?:[\s,]+(?:please|pls|plz|thanks|thank\s+you|thx|for\s+me|now))+[\s.!?]*$|[\s.!?]+$", re.I)

_ADD_VERB = r"(?:add|put|stick|pop|write(?:\s+down)?|jot(?:\s+down)?|note(?:\s+down)?|throw|chuck|save)"
_ADD_RES = (
    # add milk to my to-do list / put call mum on the todo list in vool
    re.compile(rf"^{_ADD_VERB}\s+(?P<item>.+?)\s+(?:to|on|onto|in|into)\s+{_LIST_REF}{_IN_VOOL}$", re.I),
    # add to my to-do list: milk / add to my todos call mum
    re.compile(rf"^{_ADD_VERB}\s+(?:to|on|onto)\s+{_LIST_REF}{_IN_VOOL}\s*[:,\-]?\s+(?P<item>.+)$", re.I),
    # new to-do: call mum / add a todo: call mum / to-do: call mum
    re.compile(r"^(?:(?:add\s+(?:a|an(?:other)?)|new|another)\s+)?to[\-_ ]?do\s*[:\-]\s*(?P<item>.+)$", re.I),
)
_TICK_VERB = r"(?:tick|check|cross|strike|scratch)"
_LIST_TAIL = rf"(?:\s+(?:from|on|off|in)\s+{_LIST_REF}{_IN_VOOL})?"
_DONE_RES = (
    re.compile(rf"^{_TICK_VERB}\s+off\s+(?P<item>.+?){_LIST_TAIL}$", re.I),
    re.compile(rf"^{_TICK_VERB}\s+(?P<item>.+?)\s+off(?:\s+(?:of\s+)?{_LIST_REF}{_IN_VOOL}|{_LIST_TAIL})$", re.I),
    re.compile(
        rf"^mark\s+(?P<item>.+?)\s+(?:as\s+)?(?:done|complete|completed|finished|ticked(?:\s+off)?){_LIST_TAIL}$",
        re.I,
    ),
    re.compile(rf"^(?:complete|finish)\s+(?P<item>.+?)\s+(?:on|from|in)\s+{_LIST_REF}{_IN_VOOL}$", re.I),
)
_REMOVE_VERB = r"(?:remove|delete|take|drop|erase|get\s+rid\s+of)"
_REMOVE_RES = (
    re.compile(rf"^{_REMOVE_VERB}\s+(?P<item>.+?)\s+(?:off|from|out\s+of)\s+{_LIST_REF}{_IN_VOOL}$", re.I),
    re.compile(rf"^{_REMOVE_VERB}\s+(?:from|off)\s+{_LIST_REF}{_IN_VOOL}\s*[:,\-]?\s+(?P<item>.+)$", re.I),
)

# A read: the file lane's own cues plus a few more ways people ask.
_READ_CUE_RE = re.compile(
    r"\bwhat(?:'s|s| is| was| are)?\s+(?:on|in|left\s+on|still\s+on)\b"
    r"|\bwhat\s+(?:do|did|have)\s+i\s+(?:got\s+|still\s+have\s+|have\s+)?(?:on|in)\b"
    r"|\bwhat\s+(?:else\s+)?(?:is|do\s+i\s+have)\s+(?:left\s+)?(?:on|in)\b"
    r"|\bread\s+(?:me|my|the|us|out)\b"
    r"|\bshow\s+(?:me\s+)?(?:my|the|us|what)\b"
    r"|\blist\s+(?:my|the|all)\b"
    r"|\bopen\s+(?:my|the)\b"
    r"|\bcheck\s+(?:my|the)\b"
    r"|\b(?:remind|tell)\s+me\s+what\b"
    r"|\bsee\s+(?:my|the|what)\b"
    r"|\blook\s+at\s+(?:my|the)\b"
    r"|\bgo\s+(?:over|through)\s+(?:my|the)\b"
    r"|\bpull\s+up\s+(?:my|the)\b"
    r"|\bhow\s+many\s+(?:things|items|tasks|to-?dos?)\b"
    r"|\b(?:is\s+there|do\s+i\s+have|have\s+i\s+got|got)\s+anything\b"
    r"|\banything\s+(?:left|else|still)?\s*on\b"
    r"|\bwhat\s+did\s+i\s+(?:put|write|add)\s+(?:on|in|to)\b"
    r"|\bwhat\s+(?:have|did)\s+i\s+(?:already\s+)?(?:ticked|crossed|checked|done|finished|completed|tick|cross|check|finish|complete)\b",
    re.I,
)
# "the list you keep" names VOOL's list without the noun.
_KEPT_LIST_RE = re.compile(r"\blist\s+(?:that\s+)?you(?:'re|\s+are)?\s+keep(?:ing)?\b", re.I)
_BARE_RE = re.compile(rf"^(?:(?:my|the|our)\s+)?{_VOOL_WORD}{_NOUN}{_IN_VOOL}$", re.I)
_DONE_WORDS_RE = re.compile(
    r"\b(?:done|finished|completed|ticked(?:\s+off)?|crossed(?:\s+off)?|checked\s+off)\b", re.I
)
# Changes to the whole list this lane does not make. Making or writing a new list ("make me a to do
# list for the trip") is a writing ask and is not here.
_UNSUPPORTED_RE = re.compile(
    rf"^(?:clear|empty|wipe|reset|sort|reorder|re-?order|prioriti[sz]e|rename|edit|change|update|share|send|"
    rf"e-?mail|print|export|delete|remove|erase)\b(?:\s+(?:out|all\s+of|everything\s+(?:on|in)))?\s+{_LIST_REF}{_IN_VOOL}\b",
    re.I,
)

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10,
}
_NUMBER_RE = re.compile(
    r"^(?:the\s+)?(?:(?:number|no\.?|item|#)\s*(?P<num>\d{1,3})|(?P<bare>\d{1,3})|"
    r"(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|4th|5th)"
    r"(?:\s+(?:one|item|thing))?|(?:number|item)\s+(?P<word>one|two|three|four|five|six|seven|eight|nine|ten)|"
    r"(?P<last>last)\s+(?:one|item|thing))$",
    re.I,
)
_ITEM_LEAD_RE = re.compile(r"^(?:the\s+(?=item|thing|one)|item\s+|thing\s+)", re.I)
_ITEM_QUOTES = "\"'“”‘’`"


@dataclass(frozen=True)
class TodoAsk:
    kind: str  # "add" | "done" | "remove" | "list" | "unsupported"
    target: str  # "vool" | "file" | "plain"
    items: tuple[str, ...] = field(default_factory=tuple)
    words: str = ""
    include_done: bool = False
    noun_named: bool = False


def _clean_turn(text: str) -> str:
    value = " ".join(str(text or "").replace("’", "'").replace("‘", "'").split())
    for _ in range(3):
        stripped = _LEAD_RE.sub("", value, count=1).strip()
        if stripped == value:
            break
        value = stripped
    value = _TAIL_RE.sub("", value).strip()
    return value


def _strip_item(item: str) -> str:
    value = item.strip().strip(_ITEM_QUOTES).strip()
    value = re.sub(r"^(?:that\s+i\s+(?:need|have)\s+to\s+|that\s+i\s+should\s+|i\s+(?:need|have)\s+to\s+|to\s+)", "", value, flags=re.I)
    return value.strip().strip(_ITEM_QUOTES).strip(" ,.;:")


def _split_items(item: str) -> tuple[str, ...]:
    """"milk, eggs and bread" is three items; a single "and" ("call mum and dad") is one."""
    value = _strip_item(item)
    if not value:
        return ()
    if "," in value or ";" in value:
        parts = [p for p in re.split(r"\s*[,;]\s*", value) if p.strip()]
        last = parts[-1]
        tail = re.split(r"\s+(?:and|&)\s+", re.sub(r"^(?:and|&)\s+", "", last, flags=re.I), maxsplit=1)
        parts = parts[:-1] + [t for t in tail if t.strip()]
        items = tuple(c for c in (_strip_item(re.sub(r"^(?:and|&)\s+", "", p, flags=re.I)) for p in parts) if c)
        return items
    return (value,)


def _target(text: str) -> str:
    if _FILE_CUE_RE.search(text):
        return "file"
    if _VOOL_CUE_RE.search(text):
        return "vool"
    return "plain"


def read_turn(text: str) -> TodoAsk | None:
    """What the turn asks of a to-do list, or ``None`` when it asks nothing of one."""
    raw = _clean_turn(text)
    if not raw or len(raw) > 600:
        return None
    noun = bool(_NOUN_RE.search(raw) or _KEPT_LIST_RE.search(raw))
    target = _target(raw)
    for pattern in _ADD_RES:
        match = pattern.match(raw)
        if match is not None:
            whole_list = raw[: match.start("item")] + raw[match.end("item"):]
            items = _split_items(match.group("item"))
            if not items:
                return None
            named = bool(_NOUN_RE.search(whole_list) or _VOOL_CUE_RE.search(whole_list))
            return TodoAsk("add", _target(whole_list), items=items, noun_named=named)
    for kind, patterns in (("done", _DONE_RES), ("remove", _REMOVE_RES)):
        for pattern in patterns:
            match = pattern.match(raw)
            if match is not None:
                words = _strip_item(_ITEM_LEAD_RE.sub("", match.group("item").strip(), count=1) or match.group("item"))
                if not words:
                    return None
                tail = raw[: match.start("item")] + raw[match.end("item"):]
                return TodoAsk(kind, _target(tail), words=words, noun_named=bool(_NOUN_RE.search(tail)))
    if not noun:
        return None
    if _UNSUPPORTED_RE.match(raw):
        return TodoAsk("unsupported", target, words=raw.split()[0].lower(), noun_named=True)
    if _BARE_RE.match(raw) or _READ_CUE_RE.search(raw):
        # A write verb anywhere in a read-shaped ask ("make me a to do list") is not a read.
        if re.search(r"\b(?:make|create|write|draft|plan|build|start|generate|come\s+up\s+with)\b", raw, re.I):
            return None
        return TodoAsk("list", target, include_done=bool(_DONE_WORDS_RE.search(raw)), noun_named=True)
    return None


# --------------------------------------------------------------------------------------------
# which list a number means
# --------------------------------------------------------------------------------------------

_SHOWN: OrderedDict[str, list[str]] = OrderedDict()
_SHOWN_LOCK = threading.Lock()


def _remember_shown(session_id: str, task_ids: list[str]) -> None:
    if not session_id:
        return
    with _SHOWN_LOCK:
        _SHOWN[session_id] = list(task_ids)
        _SHOWN.move_to_end(session_id)
        while len(_SHOWN) > 256:
            _SHOWN.popitem(last=False)


_LAST: OrderedDict[str, str] = OrderedDict()
_PRONOUNS = frozenset({"it", "that", "this", "that one", "this one", "that item", "this item"})


def _remember_last(session_id: str, task_id: str) -> None:
    if not session_id or not task_id:
        return
    with _SHOWN_LOCK:
        _LAST[session_id] = task_id
        _LAST.move_to_end(session_id)
        while len(_LAST) > 256:
            _LAST.popitem(last=False)


def _last(session_id: str) -> str | None:
    with _SHOWN_LOCK:
        return _LAST.get(session_id)


def _shown(session_id: str) -> list[str] | None:
    with _SHOWN_LOCK:
        value = _SHOWN.get(session_id)
        return list(value) if value is not None else None


def _number_of(words: str) -> int | None:
    match = _NUMBER_RE.match(words.strip())
    if match is None:
        return None
    if match.group("num"):
        return int(match.group("num"))
    if match.group("bare"):
        return int(match.group("bare"))
    if match.group("ord"):
        return _ORDINALS[match.group("ord").lower()]
    if match.group("word"):
        return _ORDINALS[match.group("word").lower()]
    if match.group("last"):
        return -1
    return None


# --------------------------------------------------------------------------------------------
# who may use it
# --------------------------------------------------------------------------------------------


def _who(source_context: Mapping[str, Any] | None) -> str:
    """"owner", "phone", or "" (not this lane's: keep the lanes the turn has)."""
    from core.request_trust import (
        remote_turn_device,
        request_is_owner_local,
        turn_is_owner_authored,
        turn_is_remote,
    )

    sc = dict(source_context or {})
    if not turn_is_owner_authored(sc):
        return ""
    if turn_is_remote(sc):
        return "phone" if remote_turn_device(sc) is not None else ""
    return "owner" if request_is_owner_local(sc) else ""


def _private(source_context: Mapping[str, Any] | None, session_id: str) -> bool:
    try:
        from core.private_chat import turn_is_private

        return bool(turn_is_private(dict(source_context or {}), session_id))
    except Exception:
        return True


# --------------------------------------------------------------------------------------------
# replies
# --------------------------------------------------------------------------------------------

PRIVATE_ADD_TEXT = (
    "Private chat is on, so I won't add that to your to-do list. "
    "Say \"turn recording back on\" first, then tell me again and I'll add it."
)
UNSUPPORTED_TEXT = (
    "I can add things to your to-do list, tick them off, take them off and show the list. "
    "I can't {verb} the whole list, so nothing changed."
)


def _q(title: str) -> str:
    return f"“{title}”"


def _numbered(tasks: list[dict[str, Any]]) -> str:
    return "\n".join(f"{n}. {t['title']}" for n, t in enumerate(tasks, start=1))


def _left_line(open_count: int) -> str:
    if open_count == 0:
        return "Nothing left on your to-do list."
    return f"{open_count} thing{'s' if open_count != 1 else ''} left on your to-do list."


_HINTED: OrderedDict[str, bool] = OrderedDict()


def _file_hint(session_id: str) -> str:
    """One line naming a to-do FILE on this computer, or "" when there is none, it cannot be
    looked for, or this chat already heard it. Only ever called for the owner's own local turn."""
    with _SHOWN_LOCK:
        if session_id and session_id in _HINTED:
            return ""
    try:
        from core.agent_runtime.fast_paths_machine import _todo_label, _todo_list_files

        matches = _todo_list_files(["~/Desktop", "~/Documents", "~/Downloads"])
    except Exception:
        return ""
    if not matches:
        return ""
    label = _todo_label(matches[0][1])
    if session_id:
        with _SHOWN_LOCK:
            _HINTED[session_id] = True
            while len(_HINTED) > 256:
                _HINTED.popitem(last=False)
    return f"This is the list I keep in VOOL. You also have a to-do file, {label}. Say \"read my to-do file\" to see it."


def _list_reply(session_id: str, *, include_done: bool, hint: str) -> str:
    tasks = pa_tasks.list_tasks(include_done=include_done)
    open_tasks = [t for t in tasks if t["status"] == pa_tasks.STATUS_OPEN]
    done = [t for t in tasks if t["status"] == pa_tasks.STATUS_DONE][:20]
    _remember_shown(session_id, [t["task_id"] for t in open_tasks])
    if open_tasks:
        body = f"Your to-do list:\n{_numbered(open_tasks)}"
    else:
        body = "Your to-do list is empty."
    if include_done:
        body += "\n\nTicked off:\n" + "\n".join(f"- {t['title']}" for t in done) if done else "\n\nNothing ticked off yet."
    if hint:
        body += f"\n\n{hint}"
    return body


def _resolve(words: str, session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(matches, open items). A number means the list last shown in this chat, else the list now;
    "it" means the item this chat last added, ticked off or named."""
    open_tasks = pa_tasks.list_tasks()
    if words.strip().lower() in _PRONOUNS:
        last = _last(session_id)
        task = pa_tasks.get_task(last) if last else None
        return ([task] if task is not None else []), open_tasks
    number = _number_of(words)
    if number is not None:
        shown = _shown(session_id)
        if shown is not None:
            by_id = {t["task_id"]: t for t in pa_tasks.list_tasks(include_done=True)}
            order = [by_id.get(task_id) for task_id in shown]
        else:
            order = list(open_tasks)
        if number == -1:
            number = len(order)
        if 1 <= number <= len(order) and order[number - 1] is not None:
            return [order[number - 1]], open_tasks
        return [], open_tasks
    return pa_tasks.find_open_tasks(words, tasks=open_tasks), open_tasks


def _done_already(words: str) -> dict[str, Any] | None:
    done = [t for t in pa_tasks.list_tasks(include_done=True) if t["status"] == pa_tasks.STATUS_DONE]
    if not done:
        return None
    key = pa_tasks._match_key(words)
    for task in done:
        if pa_tasks._match_key(task["title"]) == key:
            return task
    return None


def _ambiguous_reply(words: str, matches: list[dict[str, Any]], session_id: str, verb: str) -> str:
    _remember_shown(session_id, [t["task_id"] for t in matches])
    return (
        f"More than one item on your to-do list matches {_q(words)}:\n{_numbered(matches)}\n"
        f"Which one? Say the number, like \"{verb} 1\". Nothing changed."
    )


def _not_found_reply(words: str, open_tasks: list[dict[str, Any]], session_id: str) -> str:
    _remember_shown(session_id, [t["task_id"] for t in open_tasks])
    if not open_tasks:
        return f"I couldn't find {_q(words)} on your to-do list. It's empty, so nothing changed."
    return (
        f"I couldn't find {_q(words)} on your to-do list, so nothing changed. "
        f"Here is what's on it:\n{_numbered(open_tasks)}"
    )


def _answer(ask: TodoAsk, *, session_id: str, who: str, source_context: Mapping[str, Any] | None) -> str | None:
    in_use = pa_tasks.has_any_task()
    if ask.kind == "list":
        if ask.target == "file" or (ask.target == "plain" and not in_use):
            return None
        hint = _file_hint(session_id) if (ask.target == "plain" and who == "owner") else ""
        return _list_reply(session_id, include_done=ask.include_done, hint=hint)
    if ask.target == "file":
        return None
    if ask.kind == "unsupported":
        if ask.target == "plain" and not in_use:
            return None
        return UNSUPPORTED_TEXT.format(verb=ask.words)
    if ask.kind == "add":
        if not ask.noun_named and not in_use:
            # "add eggs to my list" before the person ever kept a VOOL list: some other list.
            return None
        if _private(source_context, session_id):
            return PRIVATE_ADD_TEXT
        open_tasks = pa_tasks.list_tasks()
        open_keys = {pa_tasks._match_key(t["title"]) for t in open_tasks}
        added: list[str] = []
        already: list[str] = []
        for item in ask.items:
            title = pa_tasks.clean_title(item)
            key = pa_tasks._match_key(title)
            if not key:
                continue
            if key in open_keys:
                already.append(title)
                continue
            created = pa_tasks.create_task(title=title)
            _remember_last(session_id, created["task_id"])
            open_keys.add(key)
            added.append(title)
        open_count = len(pa_tasks.list_tasks())
        parts = []
        if len(added) == 1:
            parts.append(f"Added {_q(added[0])} to your to-do list.")
        elif added:
            parts.append(f"Added {len(added)} things to your to-do list: {', '.join(_q(a) for a in added)}.")
        if already:
            if len(already) == 1:
                parts.append(f"{_q(already[0])} is already on it.")
            else:
                parts.append(f"{', '.join(_q(a) for a in already)} are already on it.")
        if not parts:
            return None
        parts.append(f"{open_count} thing{'s' if open_count != 1 else ''} on it now.")
        return " ".join(parts)
    # done / remove
    number = _number_of(ask.words)
    pronoun = ask.words.strip().lower() in _PRONOUNS
    if not ask.noun_named:
        # No list named: a number or "it" counts only against what this chat just showed or did,
        # and plain words only when they name an item on the list.
        if not in_use:
            return None
        if number is not None and _shown(session_id) is None:
            return None
        if pronoun and _last(session_id) is None:
            return None
    matches, open_tasks = _resolve(ask.words, session_id)
    verb = "tick off" if ask.kind == "done" else "take off"
    if not matches:
        if not ask.noun_named and number is None and not pronoun:
            return None
        if ask.kind == "done":
            already = _done_already(ask.words)
            if already is not None:
                return f"{_q(already['title'])} is already ticked off. {_left_line(len(open_tasks))}"
        if pronoun:
            return "I'm not sure which item you mean, so nothing changed. " + (
                f"Here is what's on your to-do list:\n{_numbered(open_tasks)}" if open_tasks else "Your to-do list is empty."
            )
        return _not_found_reply(ask.words, open_tasks, session_id)
    if len(matches) > 1:
        return _ambiguous_reply(ask.words, matches, session_id, verb)
    task = matches[0]
    _remember_last(session_id, task["task_id"])
    if ask.kind == "done":
        if task.get("status") == pa_tasks.STATUS_DONE:
            return f"{_q(task['title'])} is already ticked off. {_left_line(len(open_tasks))}"
        result = pa_tasks.complete_task(task["task_id"])
        if result.get("status") == "already_done":
            return f"{_q(task['title'])} is already ticked off. {_left_line(len(pa_tasks.list_tasks()))}"
        if not result.get("ok"):
            return _not_found_reply(ask.words, pa_tasks.list_tasks(), session_id)
        return f"Ticked off {_q(task['title'])}. {_left_line(len(pa_tasks.list_tasks()))}"
    result = pa_tasks.remove_task(task["task_id"])
    if not result.get("ok"):
        return _not_found_reply(ask.words, pa_tasks.list_tasks(), session_id)
    return f"Took {_q(task['title'])} off your to-do list. {_left_line(len(pa_tasks.list_tasks()))}"


def maybe_answer(
    agent: Any,
    *,
    text: str,
    session_id: str,
    source_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The turn's answer from the VOOL to-do list, or ``None`` to keep the lanes it has."""
    ask = read_turn(text)
    if ask is None:
        return None
    who = _who(source_context)
    if not who:
        return None
    try:
        response = _answer(ask, session_id=str(session_id or ""), who=who, source_context=source_context)
    except Exception:
        # The store could not be read or written: say so plainly, never claim a change.
        response = "I couldn't reach your to-do list just now, so nothing changed. Please try again."
    if response is None:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=text,
        response=response,
        confidence=0.95,
        source_context=source_context,
        reason=ROUTE_REASON,
    )


__all__ = ["PRIVATE_ADD_TEXT", "ROUTE_REASON", "UNSUPPORTED_TEXT", "TodoAsk", "maybe_answer", "read_turn"]

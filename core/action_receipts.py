"""Action receipts: what a consequential action actually did, read from its result and checked.

THE GAP THIS CLOSES
-------------------
A reply that says "Done!" is the model's words. The false-"done" gate (core/action_claim_gate.py)
already withholds a claim no tool backs, and the proof chip lists the tool that ran as
"executed". Neither tells a person WHAT happened: the time the reminder is set for, where the file
went, whether the email left, or why it did not. That fact sits in the tool's own result
(``details``) and in the store the tool wrote, never in the answer.

THE SEAM
--------
`execute_runtime_tool` (core/runtime_execution_tools.py) is the one door every runtime tool effect
passes, and the front door's operator step (core/agent_runtime/turn_dispatch.py) is the other.
Both call `note_action(...)` with the result they got. A receipt is filed only for an action that
changes something (its declared side-effect class is not ``read_only``, or its result names a
stored effect), and its state is decided here, in this order:

    waiting   the action is held for the owner's approval; nothing has happened yet
    failed    the tool said it did not do it; the reason is the tool's own first line
    checked   the tool said done AND a read-back of the store or the disk agrees
    sent      a message was handed to a mail server or a chat service (delivery is not knowable)
    done      the tool said done and nothing here can read it back
    mismatch  the tool said done and the read-back disagrees (the reminder or file is not there)

The receipt carries typed facts (a due instant with its zone, a file name and folder, a recipient),
never prose the model wrote. It rides on the turn's event log as ``action_receipt`` and is read
back for the answer it belongs to by core/proof_projection.py.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

EVENT_TYPE = "action_receipt"

#: Statuses a tool uses for "held until the owner approves".
_WAITING_WORDS = ("approval", "pending", "awaiting", "confirm")
#: Result fields that name a stored effect even when no side-effect class is declared.
_EFFECT_FIELDS = ("reminder_id", "destination_path", "message_id", "contact_id", "written_path", "event_id", "note_id")


#: The runtime's own refusal prefix names the tool by its id; a person reads the reason after it.
_TOOL_ID_PREFIX = re.compile(r"^`[\w.:-]+`\s+was not executed:\s*")


def _first_line(text: Any) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            line = _TOOL_ID_PREFIX.sub("", line.strip())
            line = line[:1].upper() + line[1:]
            return line if len(line) <= 220 else line[:217] + "..."
    return ""


def _side_effect_class(intent: str) -> str:
    try:
        from core.tool_argument_aliases import side_effect_class_for_intent

        return side_effect_class_for_intent(intent)
    except Exception:
        return ""


def _consequential(intent: str, details: dict[str, Any]) -> bool:
    effect_class = _side_effect_class(intent)
    if effect_class == "read_only":
        return False
    if effect_class:
        return True
    return any(details.get(name) for name in _EFFECT_FIELDS)


def _reminder_row(reminder_id: str) -> dict[str, Any] | None:
    from core.operator import reminders
    from storage.db import get_connection

    return reminders.load_reminder(reminder_id, get_connection_fn=get_connection)


def _read_back(intent: str, details: dict[str, Any]) -> bool | None:
    """True: the store or disk agrees with the result. False: it disagrees. None: nothing to read."""
    try:
        reminder_id = str(details.get("reminder_id") or "").strip()
        if reminder_id:
            row = _reminder_row(reminder_id)
            status = str((row or {}).get("status") or "").lower()
            if intent.endswith("cancel_reminder") or "cancel" in str(details.get("effect") or ""):
                return row is None or status in {"cancelled", "canceled"}
            if row is None or status in {"cancelled", "canceled"}:
                return False
            due = str(details.get("due_at_utc") or "")
            return not due or str(row.get("due_at_utc") or "") == due
        destination = str(details.get("destination_path") or details.get("written_path") or "").strip()
        if destination:
            if not Path(destination).expanduser().exists():
                return False
            source = str(details.get("source_path") or "").strip()
            return not (source and source != destination and Path(source).expanduser().exists())
        contact_id = str(details.get("contact_id") or "").strip()
        if contact_id and not intent.endswith("delete"):
            from core.contacts.store import get_contact

            return get_contact(contact_id) is not None
    except Exception:
        return None
    return None


def receipt_for(intent: str, *, ok: bool, status: str, response_text: str, details: dict[str, Any] | None) -> dict[str, Any] | None:
    """The receipt of one action result, or None when the action changes nothing."""
    clean_intent = str(intent or "").strip()
    facts = dict(details or {})
    if not clean_intent or not _consequential(clean_intent, facts):
        return None
    status_word = str(status or "").lower()
    checked: bool | None = None
    if not ok:
        state = "waiting" if any(word in status_word for word in _WAITING_WORDS) and facts.get("executed") is not True else "failed"
    else:
        checked = _read_back(clean_intent, facts)
        if checked is True:
            state = "checked"
        elif checked is False:
            state = "mismatch"
        elif facts.get("message_id") or _side_effect_class(clean_intent) in {"network_send", "network_publish"}:
            state = "sent"
        else:
            state = "done"
    path = str(facts.get("destination_path") or facts.get("written_path") or "")
    return {
        "action_intent": clean_intent,
        "action_state": state,
        "action_reason": _first_line(response_text) if state in {"failed", "waiting"} else "",
        "action_note": str(facts.get("note") or facts.get("title") or facts.get("display_name") or "")[:160],
        "action_due_utc": str(facts.get("due_at_utc") or facts.get("start_utc") or ""),
        "action_due_tz": str(facts.get("tz_name") or ""),
        "action_file": Path(path).name if path else "",
        "action_folder": str(Path(path).parent) if path else "",
        "action_to": str(facts.get("to") or facts.get("recipient") or "")[:160],
    }


#: Receipts already filed, by (turn, action, effect identity). A model-loop call that runs through the
#: runtime tool door reaches both seams with the same result; it is one action and one receipt.
_FILED: OrderedDict[tuple[str, str, str], None] = OrderedDict()
_FILED_LOCK = threading.Lock()
_FILED_MAX = 4096


def _first_filing(source_context: dict[str, Any], intent: str, receipt: dict[str, Any], details: dict[str, Any]) -> bool:
    turn = str(source_context.get("request_id") or source_context.get("turn_id") or source_context.get("cancel_turn_id") or "")
    identity = "|".join(str(details.get(name) or "") for name in _EFFECT_FIELDS) + "|" + "|".join(
        str(receipt.get(name) or "") for name in ("action_state", "action_reason", "action_due_utc", "action_note"))
    key = (turn, intent, identity)
    with _FILED_LOCK:
        if key in _FILED:
            return False
        _FILED[key] = None
        while len(_FILED) > _FILED_MAX:
            _FILED.popitem(last=False)
    return True


def note_action(source_context: dict[str, Any] | None, intent: str, *, ok: bool, status: str,
                response_text: str, details: dict[str, Any] | None) -> None:
    """File the receipt of one action result on its turn's event log. Never raises."""
    try:
        if not isinstance(source_context, dict):
            return
        if not str(source_context.get("runtime_session_id") or source_context.get("session_id") or "").strip():
            return
        receipt = receipt_for(intent, ok=ok, status=status, response_text=response_text, details=details)
        if receipt is None or not _first_filing(source_context, intent, receipt, dict(details or {})):
            return
        from core.runtime_task_events import emit_runtime_event

        emit_runtime_event(
            source_context,
            event_type=EVENT_TYPE,
            message=f"{intent}: {receipt['action_state']}",
            details=receipt,
        )
    except Exception:
        return


#: Statuses of a consequential call stopped before it reached the runtime tool door.
HELD_OR_REFUSED_STATUSES = frozenset({"pending_approval", "approval_required", "concurrent_approval", "blocked_by_mode"})


def note_held_or_refused(source_context: dict[str, Any] | None, intent: str, result: Any) -> None:
    """The receipt of a call an approval boundary held or refused: it never reached the tool door,
    which files the receipt of every call that does, so it is filed here. Never raises."""
    try:
        status = str(getattr(result, "status", "") or "")
        details = dict(getattr(result, "details", None) or {})
        if result is None or status not in HELD_OR_REFUSED_STATUSES or details.get("executed") is not False:
            return
        note_action(source_context, str(intent or ""), ok=False, status=status,
                    response_text=str(getattr(result, "user_safe_response_text", "") or getattr(result, "response_text", "") or ""),
                    details=details)
    except Exception:
        return


def note_execution(source_context: dict[str, Any] | None, intent: str, result: Any) -> None:
    """The receipt of a model-loop tool execution, whichever family ran it (operator, contacts, mail...).
    A call that also crossed the runtime tool door is filed once (see `_first_filing`). Never raises."""
    try:
        if result is None or not bool(getattr(result, "handled", True)):
            return
        status = str(getattr(result, "status", "") or "")
        if status in HELD_OR_REFUSED_STATUSES:
            note_held_or_refused(source_context, intent, result)
            return
        note_action(source_context, str(intent or ""), ok=bool(getattr(result, "ok", False)), status=status,
                    response_text=str(getattr(result, "user_safe_response_text", "") or getattr(result, "response_text", "") or ""),
                    details=dict(getattr(result, "details", None) or {}))
    except Exception:
        return


#: The action a saved memory is filed under: the approval catalog already names it in every language.
MEMORY_KEEP_INTENT = "profile.remember"


def _memory_row_active(fact: str) -> bool | None:
    """True: a live memory row holds exactly this fact. False: none does. None: the store cannot be read."""
    try:
        from core.memory import entries

        rows = entries._load_jsonl(entries.memory_entries_path())
    except Exception:
        return None
    return any(entries._row_is_active_memory(row) and str(row.get("text") or "") == fact for row in rows)


def note_memory_keep(source_context: dict[str, Any] | None, saved: list[str], refused: list[str]) -> None:
    """The receipts of what one turn's keep step or memory lane wrote and refused (core.memory.keep_receipt).
    A saved fact is checked against the memory store, never against the reply's words; a refused write is
    "not done" (the reply says why, in the person's language). Never raises."""
    try:
        if not isinstance(source_context, dict):
            return
        if not str(source_context.get("runtime_session_id") or source_context.get("session_id") or "").strip():
            return
        from core.runtime_task_events import emit_runtime_event

        filings: list[dict[str, Any]] = []
        for fact in saved or []:
            present = _memory_row_active(str(fact))
            state = "checked" if present is True else ("mismatch" if present is False else "done")
            filings.append({**_blank_receipt(), "action_state": state, "action_note": str(fact)[:160]})
        for reason in refused or []:
            filings.append({**_blank_receipt(), "action_state": "failed", "action_refusal": str(reason)[:40]})
        for receipt in filings:
            identity = {"note_id": receipt["action_note"] or receipt.get("action_refusal", "")}
            if not _first_filing(source_context, MEMORY_KEEP_INTENT, receipt, identity):
                continue
            emit_runtime_event(source_context, event_type=EVENT_TYPE,
                               message=f"{MEMORY_KEEP_INTENT}: {receipt['action_state']}", details=receipt)
    except Exception:
        return


def _blank_receipt() -> dict[str, Any]:
    return {"action_intent": MEMORY_KEEP_INTENT, "action_state": "", "action_reason": "", "action_note": "",
            "action_due_utc": "", "action_due_tz": "", "action_file": "", "action_folder": "", "action_to": ""}


def receipts_of_events(turn_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn's action receipts in order, one per filed event."""
    rows: list[dict[str, Any]] = []
    for event in turn_events:
        if str(event.get("event_type") or "") != EVENT_TYPE or not event.get("action_intent"):
            continue
        intent = str(event.get("action_intent"))
        rows.append({
            "intent": intent,
            "action_key": "approval.action." + intent,
            "state": str(event.get("action_state") or "done"),
            "reason": str(event.get("action_reason") or ""),
            "note": str(event.get("action_note") or ""),
            "due_utc": str(event.get("action_due_utc") or ""),
            "due_tz": str(event.get("action_due_tz") or ""),
            "file": str(event.get("action_file") or ""),
            "folder": str(event.get("action_folder") or ""),
            "to": str(event.get("action_to") or ""),
        })
    return rows


__all__ = ["EVENT_TYPE", "HELD_OR_REFUSED_STATUSES", "MEMORY_KEEP_INTENT", "note_action", "note_execution", "note_held_or_refused", "note_memory_keep", "receipt_for", "receipts_of_events"]

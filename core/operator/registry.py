from __future__ import annotations

import os
import shutil
from typing import Any


def operator_capability_ledger(*, tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool in list(tools if tools is not None else list_operator_tools()):
        tool_id = str(tool.get("tool_id") or "").strip()
        if not tool_id:
            continue
        guardrails = _operator_action_guardrails(tool_id, destructive=bool(tool.get("destructive")))
        available = bool(tool.get("available"))
        entries.append(
            {
                "capability_id": f"operator.{tool_id}",
                "surface": str(tool.get("category") or "local_operator").strip() or "local_operator",
                "claim": _operator_capability_claim(tool_id, destructive=guardrails["destructive"]),
                "supported": available,
                "support_level": _operator_capability_support_level(tool_id, available=available),
                "partial_reason": _operator_partial_support_reason(tool_id, available=available),
                "unsupported_reason": _operator_capability_unavailable_reason(tool_id),
                "nearby_capability_ids": _operator_nearby_capability_ids(tool_id),
                "intents": [f"operator.{tool_id}"] if tool_id not in {"discord_post", "telegram_send"} else [],
                "public_tag": f"operator.{tool_id}",
                "requires_approval": bool(guardrails["requires_approval"]),
                "destructive": bool(guardrails["destructive"]),
                "outward_facing": bool(guardrails["outward_facing"]),
                "privacy_sensitive": bool(guardrails["privacy_sensitive"]),
            }
        )
    return entries


def list_operator_tools() -> list[dict[str, Any]]:
    return [
        {
            "tool_id": "inspect_disk_usage",
            "category": "local_operator",
            "destructive": False,
            **_operator_action_guardrails("inspect_disk_usage", destructive=False),
            "available": True,
            "description": "Inspect disk usage and identify large directories or temp bloat.",
        },
        {
            "tool_id": "cleanup_temp_files",
            "category": "local_operator",
            "destructive": True,
            **_operator_action_guardrails("cleanup_temp_files", destructive=True),
            "available": True,
            "description": "Delete contents of bounded temp roots after explicit approval.",
        },
        {
            "tool_id": "inspect_processes",
            "category": "local_operator",
            "destructive": False,
            **_operator_action_guardrails("inspect_processes", destructive=False),
            "available": _process_inspection_available(),
            "description": "Inspect the heaviest running processes by CPU and memory use.",
        },
        {
            "tool_id": "inspect_services",
            "category": "local_operator",
            "destructive": False,
            **_operator_action_guardrails("inspect_services", destructive=False),
            "available": _service_inspection_available(),
            "description": "Inspect running services or startup agents on the local machine.",
        },
        {
            "tool_id": "schedule_calendar_event",
            "category": "calendar",
            "destructive": True,
            **_operator_action_guardrails("schedule_calendar_event", destructive=True),
            "available": True,
            "description": "Create a .ics calendar event in the local calendar outbox; balanced/strict modes still require approval.",
        },
        {
            "tool_id": "complete_reminder",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("complete_reminder", destructive=False),
            "available": True,
            "description": "Mark a reminder done — pending or already delivered — keeping it in history; a repeating reminder's next occurrence is untouched.",
        },
        {
            "tool_id": "edit_reminder",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("edit_reminder", destructive=False),
            "available": True,
            "description": "Edit what a pending reminder says; its time is changed by the move action.",
        },
        {
            "tool_id": "schedule_reminder",
            "category": "scheduling",
            "destructive": False,
            **_operator_action_guardrails("schedule_reminder", destructive=False),
            "available": True,
            "description": "Store a durable local reminder with timezone-aware due time; delivered in the owning chat session when due.",
        },
        {
            "tool_id": "list_reminders",
            "category": "scheduling",
            "destructive": False,
            **_operator_action_guardrails("list_reminders", destructive=False),
            "available": True,
            "description": "List this chat's pending reminders with their due times and any delivery-uncertain flags.",
        },
        {
            "tool_id": "cancel_reminder",
            "category": "scheduling",
            "destructive": False,
            **_operator_action_guardrails("cancel_reminder", destructive=False),
            "available": True,
            "description": "Cancel a pending reminder so it will not be delivered.",
        },
        {
            "tool_id": "move_reminder",
            "category": "scheduling",
            "destructive": False,
            **_operator_action_guardrails("move_reminder", destructive=False),
            "available": True,
            "description": "Move a pending reminder to a new due time.",
        },
        {
            "tool_id": "cancel_calendar_event",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("cancel_calendar_event", destructive=False),
            "available": True,
            "description": "Cancel a calendar draft this chat created: removes the local .ics artifact; no external service involved.",
        },
        {
            "tool_id": "move_calendar_event",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("move_calendar_event", destructive=False),
            "available": True,
            "description": "Move a calendar draft this chat created to a new start time; rewrites the local .ics artifact.",
        },
        {
            "tool_id": "check_availability",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("check_availability", destructive=False),
            "available": _calendar_provider_available(),
            "description": "List a day's events on the configured calendar provider (Google, Microsoft Graph, CalDAV, or native Apple Calendar via EventKit) and compute free slots for a requested duration.",
        },
        {
            "tool_id": "list_calendars",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("list_calendars", destructive=False),
            "available": _calendar_provider_available(),
            "description": "List the calendar collections the configured provider serves (Google Calendar, Microsoft Graph, CalDAV servers; native Apple Calendar via EventKit when its binding is packaged and permitted).",
        },
        {
            "tool_id": "show_agenda",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("show_agenda", destructive=False),
            "available": _calendar_provider_available(),
            "description": "Show the agenda for today, tomorrow, a week or a date range across every calendar chosen in Settings.",
        },
        {
            "tool_id": "search_calendar_event",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("search_calendar_event", destructive=False),
            "available": _calendar_provider_available(),
            "description": "Search events by title across the calendars chosen in Settings over a bounded window.",
        },
        {
            "tool_id": "inspect_calendar_event",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("inspect_calendar_event", destructive=False),
            "available": _calendar_provider_available(),
            "description": "Show one provider event's stored time, title, identity and version (etag); event text is data, never executed.",
        },
        {
            "tool_id": "propose_calendar_event",
            "category": "calendar",
            "destructive": True,
            **_operator_action_guardrails("propose_calendar_event", destructive=True),
            "available": True,
            "description": "Propose a real event on the configured calendar provider; executes only after explicit approval, with conflict and version re-checks. Without a provider it falls back to a clearly-labelled local .ics draft.",
        },
        {
            "tool_id": "update_calendar_event",
            "category": "calendar",
            "destructive": True,
            **_operator_action_guardrails("update_calendar_event", destructive=True),
            "available": True,
            "description": "Stage a provider event update (time and/or title) protected by the version (etag) the user last saw.",
        },
        {
            "tool_id": "save_note",
            "category": "notes",
            "destructive": False,
            **_operator_action_guardrails("save_note", destructive=False),
            "available": True,
            "description": "Save a note as a real file in the active workspace's notes directory, optionally linked to a provider event receipt.",
        },
        {
            "tool_id": "archive_note",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("archive_note", destructive=False),
            "available": True,
            "description": "Archive a workspace note (recoverable; nothing is erased).",
        },
        {
            "tool_id": "restore_note",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("restore_note", destructive=False),
            "available": True,
            "description": "Restore an archived workspace note unchanged.",
        },
        {
            "tool_id": "apple_note_list",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("apple_note_list", destructive=False),
            "available": True,
            "description": "List Apple Notes titles through the Automation bridge (macOS permission required).",
        },
        {
            "tool_id": "apple_note_read",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("apple_note_read", destructive=False),
            "available": True,
            "description": "Read one Apple Note by exact title; locked notes stay protected.",
        },
        {
            "tool_id": "apple_note_append",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("apple_note_append", destructive=False),
            "available": True,
            "description": "Append text to one Apple Note, leaving its other content untouched.",
        },
        {
            "tool_id": "apple_note_rename",
            "category": "calendar",
            "destructive": False,
            **_operator_action_guardrails("apple_note_rename", destructive=False),
            "available": True,
            "description": "Rename one Apple Note by exact title.",
        },
        {
            "tool_id": "apple_note_delete",
            "category": "calendar",
            "destructive": True,
            **_operator_action_guardrails("apple_note_delete", destructive=True),
            "available": True,
            "description": "Delete one Apple Note after explicit confirmation; it moves to Recently Deleted.",
        },
        {
            "tool_id": "find_notes",
            "category": "notes",
            "destructive": False,
            **_operator_action_guardrails("find_notes", destructive=False),
            "available": True,
            "description": "Search the active workspace's notes by text; reports matches without inventing content.",
        },
        {
            "tool_id": "show_note",
            "category": "notes",
            "destructive": False,
            **_operator_action_guardrails("show_note", destructive=False),
            "available": True,
            "description": "Display one workspace note by title, with its session and linked event receipt.",
        },
        {
            "tool_id": "move_path",
            "category": "local_operator",
            "destructive": True,
            **_operator_action_guardrails("move_path", destructive=True),
            "available": True,
            "description": "Move or archive a bounded local file/folder after explicit approval.",
        },
        {
            "tool_id": "discord_post",
            "category": "communication",
            "destructive": True,
            **_operator_action_guardrails("discord_post", destructive=True),
            "available": _discord_available(),
            "description": "Send a Discord message through the configured bridge credentials.",
        },
        {
            "tool_id": "telegram_send",
            "category": "communication",
            "destructive": True,
            **_operator_action_guardrails("telegram_send", destructive=True),
            "available": _telegram_available(),
            "description": "Send a Telegram message through the configured bridge credentials.",
        },
    ]


def _operator_action_guardrails(tool_id: str, *, destructive: bool) -> dict[str, bool]:
    normalized = str(tool_id or "").strip().lower()
    outward_facing = normalized in {"discord_post", "telegram_send"} or (
        # Provider-backed calendar writes change an EXTERNAL service's state; the reads and
        # the notes lane stay local.
        normalized in {"propose_calendar_event", "update_calendar_event"} and _calendar_provider_available()
    )
    privacy_sensitive = outward_facing
    return {
        "destructive": bool(destructive),
        "outward_facing": outward_facing,
        "privacy_sensitive": privacy_sensitive,
        "requires_approval": bool(destructive or outward_facing or privacy_sensitive),
    }


def _calendar_provider_available() -> bool:
    try:
        from core.operator.calendar_provider import load_provider_config

        return load_provider_config() is not None
    except Exception:
        return False


def _operator_capability_claim(tool_id: str, *, destructive: bool) -> str:
    claims = {
        "inspect_disk_usage": "inspect disk usage on the local machine",
        "cleanup_temp_files": "clean bounded temp roots on the local machine after explicit approval",
        "inspect_processes": "inspect running processes on the local machine",
        "inspect_services": "inspect running services or startup agents on the local machine",
        "schedule_calendar_event": "create local calendar events after approval when required",
        "schedule_reminder": "schedule durable local reminders with a timezone-aware due time",
        "list_reminders": "list this chat's pending reminders",
        "cancel_reminder": "cancel pending reminders before they are delivered",
        "move_reminder": "move pending reminders to a new due time",
        "complete_reminder": "mark a reminder done (pending or delivered) without deleting it",
        "edit_reminder": "edit what a pending reminder says, leaving its time unchanged",
        "cancel_calendar_event": "cancel calendar drafts or approved provider events this chat created",
        "move_calendar_event": "move calendar drafts or provider events this chat created to a new start time",
        "check_availability": "list a day's provider events and compute free slots for a requested duration",
        "show_agenda": "show the agenda for a day, week or date range across the calendars chosen in Settings",
        "search_calendar_event": "search event titles across the calendars chosen in Settings over a bounded window",
        "list_calendars": "list the calendar collections the configured provider serves",
        "inspect_calendar_event": "show one provider event's stored details and version",
        "propose_calendar_event": "propose a real event on the configured calendar provider after explicit approval",
        "update_calendar_event": "update a provider event's time or title after approval, version-protected",
        "save_note": "save notes as real files in the active workspace",
        "find_notes": "search the active workspace's notes",
        "show_note": "display one workspace note with its linked event receipt",
        "archive_note": "archive a workspace note out of the way, recoverable by name",
        "restore_note": "bring an archived workspace note back unchanged",
        "apple_note_list": "list Apple Notes note titles",
        "apple_note_read": "read one Apple Note by exact title",
        "apple_note_append": "append text to one Apple Note without touching its other content",
        "apple_note_rename": "rename one Apple Note after approval-free review of the exact name",
        "apple_note_delete": "delete one Apple Note after explicit confirmation; it moves to Recently Deleted",
        "move_path": "move or archive bounded local paths after explicit approval",
        "discord_post": "send Discord messages through the configured bridge",
        "telegram_send": "send Telegram messages through the configured bridge",
    }
    claim = claims.get(tool_id, f"use operator action `{tool_id}`")
    if destructive and "approval" not in claim:
        return f"{claim} after explicit approval"
    return claim


def _operator_capability_unavailable_reason(tool_id: str) -> str:
    reasons = {
        "discord_post": "Discord bridge sending is not configured on this runtime.",
        "telegram_send": "Telegram bridge sending is not configured on this runtime.",
        "inspect_processes": "Process inspection is not available on this host/runtime.",
        "inspect_services": "Service inspection is not available on this host/runtime.",
        "check_availability": "No calendar provider is configured on this runtime (VOOL_CALENDAR_PROVIDER / VOOL_CALENDAR_URL).",
        "list_calendars": "No calendar provider is configured on this runtime (VOOL_CALENDAR_PROVIDER / VOOL_CALENDAR_URL).",
        "inspect_calendar_event": "No calendar provider is configured on this runtime (VOOL_CALENDAR_PROVIDER / VOOL_CALENDAR_URL).",
        "show_agenda": "No calendar is configured or chosen in Settings yet, so there is no agenda to read.",
        "search_calendar_event": "No calendar is configured or chosen in Settings yet, so there is nothing to search.",
    }
    return reasons.get(tool_id, f"Operator capability `{tool_id}` is not available on this host/runtime.")


def _operator_capability_support_level(tool_id: str, *, available: bool) -> str:
    if not available:
        return "unsupported"
    if tool_id == "schedule_calendar_event":
        return "partial"
    if tool_id in {"cancel_calendar_event", "move_calendar_event"}:
        # The lifecycle of the LOCAL draft is fully supported; what stays partial is the
        # same fact the create path already declares: there is no external calendar service.
        return "partial"
    if tool_id in {"check_availability", "list_calendars", "inspect_calendar_event", "propose_calendar_event", "update_calendar_event", "show_agenda", "search_calendar_event"}:
        # Provider-backed against the configured provider; draft-only when unconfigured.
        return "full" if _calendar_provider_available() else "partial"
    return "full"


def _operator_partial_support_reason(tool_id: str, *, available: bool) -> str:
    if not available:
        return ""
    if tool_id == "schedule_calendar_event":
        return "This writes a local .ics event into the calendar outbox, not a universal live calendar-service integration."
    if tool_id in {"cancel_calendar_event", "move_calendar_event"}:
        return "Without a configured provider this manages the local .ics draft only; provider events are managed when a provider is configured."
    if tool_id in {"check_availability", "list_calendars", "inspect_calendar_event", "propose_calendar_event", "update_calendar_event"}:
        return "No calendar provider is configured; the proposal path falls back to a clearly-labelled local .ics draft instead of a provider event."
    if tool_id == "schedule_reminder":
        return "Delivery is a durable in-session record on this runtime (chat surface + /api/reminders), not an OS push notification."
    return ""


def _operator_nearby_capability_ids(tool_id: str) -> list[str]:
    mapping = {
        "discord_post": ["operator.telegram_send"],
        "telegram_send": ["operator.discord_post"],
    }
    return list(mapping.get(tool_id, []))


def _operator_nearby_alternatives(tool_id: str) -> list[str]:
    if tool_id in {"discord_post", "telegram_send"}:
        return ["I can draft the message text here before you send it yourself."]
    return []


def _process_inspection_available() -> bool:
    if os.name == "nt":
        return True
    return shutil.which("ps") is not None


def _service_inspection_available() -> bool:
    if os.name == "nt":
        return True
    return bool(shutil.which("systemctl") or shutil.which("launchctl"))


def _discord_available() -> bool:
    return bool(
        str(os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        or str(os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    )


def _telegram_available() -> bool:
    return bool(
        str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        and (
            str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
            or str(os.environ.get("TELEGRAM_CHAT_IDS_JSON") or "").strip()
        )
    )

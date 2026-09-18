from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core import audit_logger, policy_engine
from core.execution_gate import ExecutionGate
from core.kas.contract import CalendarRefusedError
from core.operator import approvals as operator_approvals
from core.operator import calendar as operator_calendar
from core.operator import calendar_provider
from core.operator import handlers as operator_handlers
from core.operator import notes as operator_notes
from core.operator import parser as operator_parser
from core.operator import registry as operator_registry
from core.operator import reminders as operator_reminders
from core.operator import storage as operator_storage
from core.operator import system as operator_system
from core.operator import when as operator_when
from core.operator.models import OperatorActionIntent, OperatorActionResult
from core.operator.parser import _extract_quoted_values
from core.runtime_paths import data_path
from storage.db import get_connection

_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_ISO_DATETIME_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?\b")
_DURATION_RE = re.compile(r"\bfor\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b", re.IGNORECASE)
_TEMPISH_NAMES = {"temp", "tmp", "cache", "caches"}


def operator_capability_ledger() -> list[dict[str, Any]]:
    return operator_registry.operator_capability_ledger(tools=list_operator_tools())


def parse_operator_action_intent(user_text: str) -> OperatorActionIntent | None:
    return operator_parser.parse_operator_action_intent(user_text)


def list_operator_tools() -> list[dict[str, Any]]:
    return operator_registry.list_operator_tools()


def dispatch_operator_action(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    composed = _composed_request_parts(intent, task_id=task_id, session_id=session_id)
    if composed is not None:
        return composed
    if intent.kind in {"schedule_reminder", "list_reminders", "cancel_reminder", "move_reminder",
                       "cancel_calendar_event", "move_calendar_event",
                       "check_availability", "list_calendars", "inspect_calendar_event",
                       "show_agenda", "search_calendar_event", "complete_reminder", "edit_reminder",
                       "archive_note", "restore_note", "apple_note_list", "apple_note_read",
                       "apple_note_append", "apple_note_rename", "apple_note_delete",
                       "save_note", "find_notes", "show_note"}:
        # These act only on this runtime's own reminder rows/outbox drafts. They still
        # cross the existing local-action policy door, like the other operator actions.
        gate = ExecutionGate.evaluate_local_action(intent.kind, destructive=False, user_approved=False)
        if gate.mode not in {"execute", "sandbox"}:
            return OperatorActionResult(ok=False, status="blocked", response_text=gate.reason,
                                        details={"kind": intent.kind, "gate_mode": gate.mode})
    if intent.kind == "list_tools":
        return _handle_list_tools(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_processes":
        return _handle_inspect_processes(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_services":
        return _handle_inspect_services(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_disk_usage":
        return _handle_inspect_disk_usage(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "cleanup_temp_files":
        return _handle_cleanup_temp_files(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "move_path":
        return _handle_move_path(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "schedule_calendar_event":
        # An approval id that names a PROVIDER proposal/update/cancel belongs to that lane;
        # only ids with no provider row fall through to the local .ics draft handler. An
        # approval id that names a provider action OWNED BY ANOTHER SESSION is a typed
        # mismatch — never re-parsed as a fresh draft request (which would answer with a
        # bogus "need a title and time" over the user's own approval words).
        if intent.action_id and _provider_action_exists(session_id=session_id, action_id=intent.action_id):
            return _handle_provider_approval(intent, task_id=task_id, session_id=session_id)
        if intent.action_id and _provider_action_exists(session_id=None, action_id=intent.action_id):
            return OperatorActionResult(
                ok=False,
                status="invalid_request",
                response_text="That approval belongs to a different chat session. Nothing was executed; approve it in the chat that proposed it.",
                details={"action_id": intent.action_id},
            )
        return _handle_schedule_calendar_event(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "schedule_reminder":
        return _handle_schedule_reminder(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "list_reminders":
        return _handle_list_reminders(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "cancel_reminder":
        return _handle_cancel_reminder(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "move_reminder":
        return _handle_move_reminder(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "complete_reminder":
        return _handle_complete_reminder(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "edit_reminder":
        return _handle_edit_reminder(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "archive_note":
        return _handle_archive_note(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "restore_note":
        return _handle_restore_note(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "apple_note_list":
        return _handle_apple_note_list(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "apple_note_read":
        return _handle_apple_note_read(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "apple_note_append":
        return _handle_apple_note_append(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "apple_note_rename":
        return _handle_apple_note_rename(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "apple_note_delete":
        return _handle_apple_note_delete(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "show_agenda":
        return _handle_show_agenda(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "search_calendar_event":
        return _handle_search_calendar(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "check_availability":
        return _handle_check_availability(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "list_calendars":
        return _handle_list_calendars(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_calendar_event":
        return _handle_inspect_calendar_event(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "propose_calendar_event":
        return _handle_propose_calendar_event(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "update_calendar_event":
        return _handle_update_calendar_event(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "save_note":
        return _handle_save_note(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "find_notes":
        return _handle_find_notes(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "show_note":
        return _handle_show_note(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "cancel_calendar_event":
        # The provider lane owns events this chat created on a configured provider — by
        # receipt, or after a restart by an exact provider title (still approval-gated,
        # uid-scoped, etag-protected). Local .ics drafts keep their existing semantics
        # when no provider target resolves.
        if _provider_configured():
            routed = _route_provider_or_draft(
                intent, session_id=session_id,
                provider_handler=lambda: _handle_provider_cancel(intent, task_id=task_id, session_id=session_id),
                honest_refusal="I don't have a provider event or local draft matching that in this chat, and I will not cancel something I cannot identify. Give its exact title or uid.",
                honest_kind=intent.kind,
            )
            if routed is not None:
                return routed
        return _handle_cancel_calendar_event(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "move_calendar_event":
        if _provider_configured():
            routed = _route_provider_or_draft(
                intent, session_id=session_id,
                provider_handler=lambda: _handle_provider_update(intent, task_id=task_id, session_id=session_id),
                honest_refusal="I don't have a provider event or local draft matching that in this chat to move. Give its exact title or uid.",
                honest_kind=intent.kind,
            )
            if routed is not None:
                return routed
        return _handle_move_calendar_event(intent, task_id=task_id, session_id=session_id)
    return OperatorActionResult(
        ok=False,
        status="unsupported",
        response_text="I recognized an operator action request, but that action is not wired on this runtime yet.",
        details={
            "kind": intent.kind,
            "capability_gap": {
                "requested_capability": f"operator.{intent.kind}",
                "requested_label": intent.kind,
                "support_level": "unsupported",
                "gap_kind": "unwired",
                "reason": f"Operator action `{intent.kind}` is not wired on this runtime.",
                "nearby_alternatives": operator_registry._operator_nearby_alternatives(intent.kind),
            },
        },
    )


def _handle_list_tools(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_list_tools(
        intent,
        task_id=task_id,
        session_id=session_id,
        operator_capability_ledger_fn=operator_capability_ledger,
        audit_log_fn=audit_logger.log,
    )


def _handle_inspect_processes(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_inspect_processes(
        intent,
        task_id=task_id,
        session_id=session_id,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        inspect_processes_fn=_inspect_processes,
        audit_log_fn=audit_logger.log,
    )


def _handle_inspect_services(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_inspect_services(
        intent,
        task_id=task_id,
        session_id=session_id,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        inspect_services_fn=_inspect_services,
        audit_log_fn=audit_logger.log,
    )


def _handle_inspect_disk_usage(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_inspect_disk_usage(
        intent,
        task_id=task_id,
        session_id=session_id,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        resolve_target_path_fn=_resolve_target_path,
        inspect_storage_fn=_inspect_storage,
        candidate_cleanup_roots_fn=_candidate_cleanup_roots,
        path_size_fn=_path_size,
        create_pending_action_fn=_create_pending_action,
        fmt_bytes_fn=_fmt_bytes,
        monotonic_fn=time.monotonic,
        audit_log_fn=audit_logger.log,
    )


def _handle_cleanup_temp_files(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_cleanup_temp_files(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_pending_action_fn=_load_pending_action,
        inspect_disk_usage_handler=_handle_inspect_disk_usage,
        operator_intent_cls=OperatorActionIntent,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        path_size_fn=_path_size,
        delete_children_fn=_delete_children,
        mark_action_executed_fn=_mark_action_executed,
        fmt_bytes_fn=_fmt_bytes,
        monotonic_fn=time.monotonic,
        audit_log_fn=audit_logger.log,
    )


def _handle_move_path(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_move_path(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_pending_action_fn=_load_pending_action,
        parse_move_request_fn=_parse_move_request,
        validate_move_scope_fn=_validate_move_scope,
        resolved_move_target_fn=_resolved_move_target,
        create_pending_action_fn=_create_pending_action,
        mark_action_executed_fn=_mark_action_executed,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        move_fn=shutil.move,
        audit_log_fn=audit_logger.log,
    )


def _handle_schedule_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_schedule_calendar_event(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_pending_action_fn=_load_pending_action,
        parse_calendar_request_fn=_parse_calendar_request,
        create_pending_action_fn=_create_pending_action,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        operator_intent_cls=OperatorActionIntent,
        render_ics_fn=_render_ics,
        mark_action_executed_fn=_mark_action_executed,
        data_path_fn=data_path,
        audit_log_fn=audit_logger.log,
    )


def _handle_schedule_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_schedule_reminder(
        intent,
        task_id=task_id,
        session_id=session_id,
        parse_when_fn=_parse_when,
        schedule_reminder_fn=_schedule_reminder,
        audit_log_fn=audit_logger.log,
    )


def _handle_list_reminders(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_list_reminders(
        intent,
        task_id=task_id,
        session_id=session_id,
        list_reminders_fn=_list_reminders,
        now_fn=lambda: datetime.now(timezone.utc).isoformat(),
        audit_log_fn=audit_logger.log,
    )


def _handle_cancel_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_cancel_reminder(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_reminder_fn=_load_reminder,
        list_reminders_fn=_list_reminders,
        cancel_reminder_fn=_cancel_reminder,
        audit_log_fn=audit_logger.log,
    )


def _handle_move_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_move_reminder(
        intent,
        task_id=task_id,
        session_id=session_id,
        parse_when_fn=_parse_when,
        load_reminder_fn=_load_reminder,
        list_reminders_fn=_list_reminders,
        move_reminder_fn=_move_reminder,
        audit_log_fn=audit_logger.log,
    )


def _handle_complete_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_complete_reminder(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_reminder_fn=_load_reminder,
        list_reminders_fn=_list_reminders,
        complete_reminder_fn=_complete_reminder,
        audit_log_fn=audit_logger.log,
    )


def _handle_edit_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_edit_reminder(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_reminder_fn=_load_reminder,
        list_reminders_fn=_list_reminders,
        edit_reminder_fn=_edit_reminder,
        audit_log_fn=audit_logger.log,
    )


def _handle_cancel_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_cancel_calendar_event(
        intent,
        task_id=task_id,
        session_id=session_id,
        load_executed_calendar_fn=_load_executed_calendar_draft,
        cancel_calendar_artifact_fn=_cancel_calendar_artifact,
        audit_log_fn=audit_logger.log,
    )


# ---------------------------------------------------------------------------
# Provider-backed calendar vertical + notes (the 02-calendar-notes mission lane)
# ---------------------------------------------------------------------------


def _provider_configured() -> bool:
    try:
        return calendar_provider.load_provider_config() is not None
    except Exception:
        # A store that cannot be read still routes to the provider lane, whose entry point
        # returns the typed storage refusal; falling to the draft lane here would bury it.
        return True


def _provider_action_exists(*, session_id: str | None, action_id: str | None) -> bool:
    """Does this approval id name a provider lane action (any state)?

    `session_id` scopes the lookup to one chat; None asks whether ANY session owns the
    id (the foreign-approval diagnosis — the answer stays a typed mismatch either way).
    """
    if not action_id:
        return False
    if session_id is None:
        from storage.db import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT action_id FROM operator_action_requests WHERE action_id = ? AND action_kind LIKE 'provider_%' LIMIT 1",
                (action_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    for kind in ("provider_calendar_event", "provider_calendar_update", "provider_calendar_cancel"):
        row = calendar_provider.load_action_any_state(session_id=session_id, action_kind=kind, action_id=action_id)
        if row is not None:
            return True
    return False


def _provider_scope():
    """The named egress scope for provider calls made outside an active turn.

    Inside a served turn this defers to the turn's own effect ledger (the gateway's
    deferral law), so production behavior is unchanged; a direct dispatch (tests, the
    operator lane's own entry points) gets the same sanctioned named scope the gateway
    documents for direct tool-intent calls.
    """
    from core.effect_gateway import named_background_effect_scope

    return named_background_effect_scope("operator.calendar_provider")


def _provider_adapter_or_refusal(action: str, *, read_only: bool = False):
    from core.operator.calendar_agenda import SelectionsUnavailableError

    try:
        config = calendar_provider.load_read_provider_config() if read_only else calendar_provider.load_provider_config()
    except SelectionsUnavailableError as exc:
        return None, OperatorActionResult(
            ok=False, status="storage_unavailable",
            response_text=(f"I could not read which calendars are chosen for sync ({exc}), so no calendar action can be "
                           "proved safe right now. Nothing was sent; check the calendar settings storage."),
            details={"reason": "selections_unavailable"},
        )
    if config is None:
        return None, calendar_provider.missing_configuration_result(action)
    with _provider_scope():
        try:
            adapter = calendar_provider.build_provider_adapter(config)
        except LookupError as exc:
            return None, OperatorActionResult(
                ok=False, status="unavailable",
                response_text=f"The configured calendar provider `{config.provider}` has no adapter on this runtime ({exc}).",
                details={"provider": config.provider},
            )
    return (config, adapter), None


def _handle_check_availability(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pair, refusal = _provider_adapter_or_refusal("check the calendar", read_only=True)
    if refusal is not None:
        return refusal
    config, adapter = pair
    with _provider_scope():
        try:
            result = calendar_provider.check_availability(
                intent, task_id=task_id, session_id=session_id, config=config, adapter=adapter,
                now_fn=_scheduling_now, audit_log_fn=audit_logger.log,
            )
        except calendar_provider.SelectionsUnavailableError as exc:
            result = OperatorActionResult(ok=False, status="storage_unavailable",
                                          response_text=f"I could not read which calendars are chosen for sync ({exc}); I will not claim free time over an unknown set.",
                                          details={"reason": "selections_unavailable"})
    if result.ok and result.details.get("options"):
        # The offered slots are the selection state for the next 'option N' reply. Reusing
        # the pending-action store keeps ONE live offer per session and no new framework.
        _create_pending_action(
            session_id=session_id, task_id=task_id, action_kind="calendar_slot_offer",
            scope={"options": result.details["options"], "zone": result.details.get("zone", "")},
        )
    return result


def _handle_archive_note(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import notes

    title = str(intent.target_label or "").strip()
    if not title:
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text="Which note should I archive? Name its title, for example: archive the note \"Kickoff\".",
                                    details={"kind": "archive_note"})
    outcome = notes.archive_note(title=title)
    audit_logger.log("operator_action_archive_note", target_id=task_id, target_type="task",
                     details={"title": title[:200], "outcome": outcome.get("status")})
    if not outcome.get("ok"):
        text = {"not_found": f"I found no note titled \"{title}\".",
                "ambiguous": f"More than one note is titled \"{title}\"; nothing was changed."}.get(
                    outcome.get("status"), f"I could not archive that note ({outcome.get('status')}).")
        return OperatorActionResult(ok=False, status="failed", response_text=text, details=outcome)
    return OperatorActionResult(ok=True, status="executed",
                                response_text=(f"Note archived: \"{title}\". It is out of the way but not erased — "
                                               f"{outcome['recovery']}."),
                                details=outcome)


def _handle_restore_note(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import notes

    title = str(intent.target_label or "").strip()
    outcome = notes.restore_note(title=title) if title else {"ok": False, "status": "not_found"}
    audit_logger.log("operator_action_restore_note", target_id=task_id, target_type="task",
                     details={"title": title[:200], "outcome": outcome.get("status")})
    if not outcome.get("ok"):
        return OperatorActionResult(ok=False, status="failed",
                                    response_text=f"I could not restore a note titled \"{title or ''}\" from the archive ({outcome.get('status')}).",
                                    details=outcome)
    return OperatorActionResult(ok=True, status="executed",
                                response_text=f"Note restored: \"{outcome['title']}\" is back and searchable.",
                                details=outcome)


def _apple_refusal_text(outcome: dict) -> str:
    detail = str(outcome.get("detail") or outcome.get("reason") or "")
    if outcome.get("reason") == "os_permission_denied":
        return detail
    return f"Apple Notes did not answer the request: {detail or outcome.get('reason')}. Nothing is claimed to have happened there."


def _apple_note_scope_words(scope: dict) -> str:
    """' in the Work folder of the iCloud account': a folder/account in words the scope reader reads back unchanged."""
    from core.operator.apple_notes import parse_notes_destination

    def _name(value: str, noun: str) -> str:
        return value if parse_notes_destination(f"in the {value} {noun}")[noun] == value else f'"{value}"'

    folder, account = str(scope.get("folder") or ""), str(scope.get("account") or "")
    words = f" in the {_name(folder, 'folder')} folder" if folder else ""
    if account:
        words += f" {'of' if folder else 'in'} the {_name(account, 'account')} account"
    return words


def _handle_apple_note_list(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import apple_notes

    outcome = apple_notes.list_apple_notes()
    audit_logger.log("operator_action_apple_note_list", target_id=task_id, target_type="task",
                     details={"ok": bool(outcome.get("ok")), "reason": outcome.get("reason")})
    if not outcome.get("ok"):
        return OperatorActionResult(ok=False, status="failed", response_text=_apple_refusal_text(outcome), details=outcome)
    destination = apple_notes.parse_notes_destination(intent.raw_text)
    names = apple_notes.notes_in_scope(outcome.get("notes") or [], folder=destination["folder"], account=destination["account"])
    scope = _apple_note_scope_words(destination)
    shown = names[:30]
    lines = [f"Apple Notes has {len(names)} note(s){scope}:"] + [f"- {name}" for name in shown]
    if len(names) > len(shown):
        lines.append(f"(first {len(shown)} shown)")
    if not names:
        lines.append(f"Apple Notes reports no notes{scope}.")
    return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines),
                                details={"notes": shown, "total": len(names), "folder": destination["folder"],
                                         "account": destination["account"]})


def _handle_apple_note_read(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import apple_notes

    title = str(intent.target_label or "").strip()
    if not title:
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text="Which Apple note should I read? Name its exact title.",
                                    details={"kind": "apple_note_read"})
    # A title alone may name several notes: the folder/account the request names narrows the read exactly as it
    # narrows a mutation.
    destination = apple_notes.parse_notes_destination(intent.raw_text)
    outcome = apple_notes.read_apple_note(title=title, folder=destination["folder"], account=destination["account"])
    audit_logger.log("operator_action_apple_note_read", target_id=task_id, target_type="task",
                     details={"title": title[:200], "folder": destination["folder"][:200], "account": destination["account"][:200],
                              "ok": bool(outcome.get("ok")), "reason": outcome.get("reason")})
    if not outcome.get("ok"):
        return OperatorActionResult(ok=False, status="failed", response_text=_apple_refusal_text(outcome), details={**outcome, "title": title})
    body = str(outcome.get("body") or "")
    preview = body[:2000]
    note = outcome.get("note") or {}
    return OperatorActionResult(ok=True, status="reported",
                                response_text=f"Apple note \"{title}\"{_apple_note_scope_words(note)}:\n{preview}"
                                + ("\n(truncated)" if len(body) > 2000 else ""),
                                details={"title": title, "body": body, "folder": note.get("folder", ""), "account": note.get("account", "")})


def _handle_apple_note_append(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import apple_notes

    title = str(intent.target_label or "").strip()
    text = str(intent.destination_path or "").strip()
    if not title or not text:
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text='Which Apple note should I append to, and with what text? For example: append to my Apple note "Standups" with "migrated repo".',
                                    details={"kind": "apple_note_append"})
    from core.operator import notes as workspace_notes

    from core.operator.apple_notes import parse_notes_destination

    destination = parse_notes_destination(intent.raw_text, data=(text,))
    outcome = workspace_notes.deliver_apple_note_mutation(
        kind="append", title=title, payload=text,
        folder=destination["folder"], account=destination["account"], task_id=task_id, session_id=session_id)
    audit_logger.log("operator_action_apple_note_append", target_id=task_id, target_type="task",
                     details={"title": title[:200], "ok": bool(outcome.get("ok")), "reason": outcome.get("reason"),
                              "replayed": bool(outcome.get("replayed"))})
    if not outcome.get("ok"):
        return OperatorActionResult(ok=False, status="failed", response_text=_apple_refusal_text(outcome), details={**outcome, "title": title})
    return OperatorActionResult(ok=True, status="executed",
                                response_text=f"Appended to the Apple note \"{title}\". The rest of the note was left untouched.",
                                details={"title": title})


def _handle_apple_note_rename(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import apple_notes

    # A quoted folder or account name is scope, never the new name: 'in the "Work" folder to "Plan v2"'.
    quoted = apple_notes.quoted_note_values(intent.raw_text)
    title = str(intent.target_label or "").strip() or (quoted[0] if quoted else "")
    new_title = apple_notes.new_note_title(intent.raw_text)
    if not title or not new_title:
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text='Rename which Apple note to what? For example: rename my Apple note "Old" to "New".',
                                    details={"kind": "apple_note_rename"})
    from core.operator import notes as workspace_notes

    from core.operator.apple_notes import parse_notes_destination

    destination = parse_notes_destination(intent.raw_text, data=(new_title,))
    outcome = workspace_notes.deliver_apple_note_mutation(
        kind="rename", title=title, new_title=new_title,
        folder=destination["folder"], account=destination["account"], task_id=task_id, session_id=session_id)
    audit_logger.log("operator_action_apple_note_rename", target_id=task_id, target_type="task",
                     details={"title": title[:200], "new_title": new_title[:200], "ok": bool(outcome.get("ok"))})
    if not outcome.get("ok"):
        return OperatorActionResult(ok=False, status="failed", response_text=_apple_refusal_text(outcome), details={**outcome, "title": title})
    return OperatorActionResult(ok=True, status="executed",
                                response_text=f"Renamed the Apple note \"{title}\" to \"{new_title}\".",
                                details={"title": title, "new_title": new_title})


def _handle_apple_note_delete(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import apple_notes

    title = str(intent.target_label or "").strip()
    if not title:
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text='Delete which Apple note? Name its exact title; I will ask you to confirm before anything is removed.',
                                    details={"kind": "apple_note_delete"})
    destination = apple_notes.parse_notes_destination(intent.raw_text)
    scope = _apple_note_scope_words(destination)
    if not intent.approval_requested:
        # The confirmation to type carries the scope as read, so confirming it deletes the note that was named.
        return OperatorActionResult(ok=False, status="approval_required",
                                    response_text=(f"This will delete the Apple note \"{title}\"{scope}. Notes moves it to "
                                                   "Recently Deleted, where macOS keeps it for its own retention period. "
                                                   f'Reply "yes, delete the apple note \"{title}\"{scope}" to confirm; if more '
                                                   "than one note has that title I will name them and ask which one."),
                                    details={"kind": "apple_note_delete", "title": title, "folder": destination["folder"],
                                             "account": destination["account"]})
    from core.operator import notes as workspace_notes

    outcome = workspace_notes.deliver_apple_note_mutation(
        kind="delete", title=title, folder=destination["folder"], account=destination["account"],
        task_id=task_id, session_id=session_id)
    audit_logger.log("operator_action_apple_note_delete", target_id=task_id, target_type="task",
                     details={"title": title[:200], "ok": bool(outcome.get("ok")), "reason": outcome.get("reason")})
    if not outcome.get("ok"):
        return OperatorActionResult(ok=False, status="failed", response_text=_apple_refusal_text(outcome), details={**outcome, "title": title})
    return OperatorActionResult(ok=True, status="executed",
                                response_text=f"Deleted the Apple note \"{title}\". It sits in Notes' Recently Deleted, not erased immediately.",
                                details={"title": title})


#: The vertical intents a composed request may carry, in execution order: reads first, then
#: artifacts. An approval id is never composed (it names ONE durable action).
_COMPOSED_KINDS = ("check_availability", "propose_calendar_event", "save_note", "schedule_reminder")



def _composed_request_parts(intent: OperatorActionIntent, *, task_id: str, session_id: str) -> OperatorActionResult | None:
    """A request naming several things at once runs EACH part and reports each outcome.

    Row 11's contract: 'inspect a week, choose a free slot, create an event, link a note and
    set an alert' must never answer one sub-action while silently dropping the rest. A part
    that refuses or fails is reported as that part's own truth; the rest still run. Each part
    goes through its ordinary handler, so its own durable store (pending action, note file,
    reminder row) and approval protections are the composed record -- there is no second
    planner. Returns None when the request is a single intent (the ordinary path).
    """
    import re as _re

    from core.operator.parser import parse_operator_action_intent

    raw = str(intent.raw_text or "")
    if intent.action_id or _re.search(r"\bapprove\b", raw, _re.IGNORECASE):
        return None
    # Recognizer co-presence (the parser module owns its regexes); the parser's own kind
    # resolution still decides whether a part actually runs.
    from core.operator import parser as _parser

    # ONLY an explicit slot reference composes a booking into the offered options: a plain
    # proposal co-present with a check keeps its own turn (the user may answer 'option 2, ...').
    books_in_slot = bool(re.search(r"\b(?:book|schedule|create|propose)\b[^\n!?.]*\b(?:free[\s-]*)?slot\b", raw, re.IGNORECASE))
    present = []
    if _parser._FREE_SLOT_RE.search(raw) or _parser._AVAILABILITY_RE.search(raw):
        present.append("check_availability")
    if books_in_slot:
        present.append("propose_calendar_event")
    if _parser._SAVE_NOTE_RE.search(raw):
        present.append("save_note")
    if _parser._REMINDER_HINT_RE.search(raw):
        present.append("schedule_reminder")
    present = [kind for kind in _COMPOSED_KINDS if kind in present]
    if len(present) < 2 or intent.kind not in present:
        return None
    results: list[OperatorActionResult] = []
    execution_order = [k for k in ("check_availability", "save_note", "propose_calendar_event", "schedule_reminder") if k in present]
    by_kind: dict[str, OperatorActionResult] = {}
    composed_note_path = ""
    for kind in execution_order:
        part_raw = raw
        if kind == "propose_calendar_event" and "check_availability" in present:
            availability = by_kind.get("check_availability")
            options = ((availability.details or {}).get("options") if availability is not None else None) or []
            if availability is None or not availability.ok:
                blocked = OperatorActionResult(ok=False, status="blocked",
                                               response_text="The booking part depends on the free-slot check, which did not answer; no event was proposed and no slot was guessed.",
                                               details={"kind": kind, "depends_on": "check_availability"})
                results.append(blocked)
                by_kind[kind] = blocked
                continue
            if not options:
                blocked = OperatorActionResult(ok=False, status="blocked",
                                               response_text="The booking part depends on the free-slot check, which found no free slot; no event was proposed.",
                                               details={"kind": kind, "depends_on": "check_availability"})
                results.append(blocked)
                by_kind[kind] = blocked
                continue
            # Deterministic, stated choice: the FIRST offered slot. The offer itself stays
            # live, so 'option 2, propose ...' redoes this part with a different slot.
            part_raw = raw + ", option 1"
        if kind == "save_note":
            part_intent = OperatorActionIntent(kind=kind, raw_text=raw)
            try:
                note_result = _handle_save_note(part_intent, task_id=task_id, session_id=session_id)
            except Exception as exc:
                note_result = OperatorActionResult(ok=False, status="failed",
                                                   response_text=f"That part failed: {type(exc).__name__}: {exc}.", details={"kind": kind})
            results.append(note_result)
            by_kind[kind] = note_result
            if note_result.ok:
                composed_note_path = str((note_result.details or {}).get("note_path") or "")
            continue
        if kind == "schedule_reminder":
            lead = re.search(r"\bremind me\b[^\n!?.]*?\b(\d{1,3})\s+minutes?\s+before\b", raw, re.IGNORECASE)
            if lead and "propose_calendar_event" in present:
                booking = by_kind.get("propose_calendar_event")
                start_utc = str(((booking.details or {}) if booking is not None else {}).get("start_utc") or "")
                if booking is None or booking.status != "approval_required" or not start_utc:
                    blocked = OperatorActionResult(ok=False, status="blocked",
                                                   response_text="The alert part depends on the event proposal, which did not stage a start time; no reminder was guessed.",
                                                   details={"kind": kind, "depends_on": "propose_calendar_event"})
                    results.append(blocked)
                    by_kind[kind] = blocked
                    continue
                minutes = int(lead.group(1))
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz

                try:
                    start = _dt.fromisoformat(start_utc.replace("Z", "+00:00"))
                except ValueError:
                    blocked = OperatorActionResult(ok=False, status="blocked",
                                                   response_text="The alert part depends on the proposal's start time, which could not be read; no reminder was guessed.",
                                                   details={"kind": kind, "depends_on": "propose_calendar_event"})
                    results.append(blocked)
                    by_kind[kind] = blocked
                    continue
                due = start - _td(minutes=minutes)
                zone = str(((booking.details or {}) if booking is not None else {}).get("tz_name") or ((availability.details or {}).get("zone") if "check_availability" in present else "") or "UTC")
                part_raw = (f"remind me about the proposed event on {due.astimezone(_tz.utc):%Y-%m-%d %H:%M} UTC "
                            f"({minutes} minutes before its start)")
        part_intent = OperatorActionIntent(kind=kind, raw_text=part_raw)
        handler = {"check_availability": _handle_check_availability,
                   "schedule_reminder": _handle_schedule_reminder,
                   "propose_calendar_event": _handle_propose_calendar_event}[kind]
        try:
            if kind == "propose_calendar_event":
                results.append(handler(part_intent, task_id=task_id, session_id=session_id,
                                       note_path_override=composed_note_path))
            else:
                results.append(handler(part_intent, task_id=task_id, session_id=session_id))
        except Exception as exc:
            failed = OperatorActionResult(ok=False, status="failed",
                                          response_text=f"That part failed: {type(exc).__name__}: {exc}.", details={"kind": kind})
            results.append(failed)
            by_kind[kind] = failed
            continue
        by_kind[kind] = results[-1]
    if not results:
        return None
    ordered = [(kind, by_kind[kind]) for kind in present if kind in by_kind]
    lines = [f"That request had {len(ordered)} parts; each is its own truth:"]
    for index, (kind, result) in enumerate(ordered, start=1):
        state = ("waiting for your approval" if result.status == "approval_required"
                 else "completed" if result.ok else f"{result.status}")
        lines.append(f"--- part {index} ({state}) ---")
        lines.append(result.response_text)
    overall_ok = all(result.ok for _, result in ordered)
    return OperatorActionResult(
        ok=overall_ok, status="reported" if overall_ok else "partial",
        response_text="\n".join(lines),
        details={"parts": [{"kind": kind, "ok": result.ok, "status": result.status, "details": result.details}
                           for kind, result in ordered]},
    )


def _handle_show_agenda(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import calendar_agenda

    start, end, understood = calendar_agenda.parse_agenda_window(intent.raw_text, now_fn=_scheduling_now)
    if start is None:
        return OperatorActionResult(ok=False, status="invalid_request", response_text=understood, details={})
    try:
        window = calendar_agenda.agenda(start_utc=start, end_utc=end)
    except calendar_agenda.SelectionsUnavailableError as exc:
        return OperatorActionResult(ok=False, status="storage_unavailable",
                                    response_text=f"I could not read which calendars are chosen for sync ({exc}); I will not guess an agenda from an unknown set.",
                                    details={"reason": "selections_unavailable"})
    if not window.get("ok") and not window.get("entries"):
        return OperatorActionResult(ok=False, status="unavailable", response_text=window.get("detail") or "No calendars are available to read.", details={"reason": window.get("reason")})
    zone_name = (load_user_timezone_name() or "UTC")
    lines = [f"Agenda for {understood} ({zone_name}), from the calendars chosen in Settings:"]
    for row in window["entries"]:
        lines.append(_agenda_line(row, zone_name))
    if not window["entries"]:
        lines.append("Nothing on the chosen calendars in that range.")
    for failure in window["failures"]:
        lines.append(f"One calendar could not be read: {failure['source']} is {failure['status']} ({failure['detail']}); it is not shown as empty.")
    audit_logger.log(
        "operator_action_show_agenda",
        target_id=task_id,
        target_type="task",
        details={"window": understood, "event_count": len(window["entries"]), "failed_sources": len(window["failures"])},
    )
    del session_id
    return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines),
                                details={"window": understood, "zone": zone_name,
                                         "events": [{"summary": row["summary"], "start_utc": row["start_utc"], "calendar": row["calendar"]} for row in window["entries"]],
                                         "failures": window["failures"], "sources": window["sources"]})


def _handle_search_calendar(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    from core.operator import calendar_agenda

    term = str(intent.target_label or "").strip()
    if not term:
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text="Which event should I search for? Name its title or a word from it.", details={})
    now = _scheduling_now().astimezone(timezone.utc)
    try:
        found = calendar_agenda.search(term=term, now_utc=now)
    except calendar_agenda.SelectionsUnavailableError as exc:
        return OperatorActionResult(ok=False, status="storage_unavailable",
                                    response_text=f"I could not read which calendars are chosen for sync ({exc}); nothing was searched.",
                                    details={"reason": "selections_unavailable"})
    if not found.get("ok") and not found.get("entries"):
        return OperatorActionResult(ok=False, status="unavailable", response_text=found.get("detail") or "No calendars are available to read.", details={"reason": found.get("reason")})
    zone_name = (load_user_timezone_name() or "UTC")
    lines = [f"Events matching \"{term}\" on the chosen calendars (searched {found['window_days_back']} days back to {found['window_days_ahead']} days ahead):"]
    for row in found["entries"]:
        lines.append(_agenda_line(row, zone_name))
    if not found["entries"]:
        lines.append(f"No event matching \"{term}\" was found. That is this search's answer, not proof none exists outside its window.")
    if found.get("truncated"):
        lines.append(f"Only the first {len(found['entries'])} matches are shown.")
    for failure in found["failures"]:
        lines.append(f"One calendar could not be read: {failure['source']} is {failure['status']} ({failure['detail']}); it was not searched.")
    del session_id
    return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines),
                                details={"term": term, "zone": zone_name, "events": found["entries"], "truncated": bool(found.get("truncated"))})


def _agenda_line(row: dict, zone_name: str) -> str:
    from zoneinfo import ZoneInfo

    # The agenda is one person's day: every line is shown in the user's stored zone, whatever
    # zone each provider stored the event in.
    zone = ZoneInfo(zone_name or "UTC")
    if row.get("all_day"):
        when = f"all-day on {row.get('start_date') or 'an unreadable date'}"
    else:
        try:
            start = datetime.fromisoformat(str(row["start_utc"]).replace("Z", "+00:00")).astimezone(zone)
            end = datetime.fromisoformat(str(row["end_utc"]).replace("Z", "+00:00")).astimezone(zone)
            when = f"{start:%Y-%m-%d %H:%M}\u2013{end:%H:%M} ({getattr(zone, 'key', zone_name)})"
        except Exception:
            when = str(row.get("start_utc") or "time not readable")
    cancelled = " [cancelled]" if row.get("cancelled") else ""
    bits = [f"- {row.get('summary') or '(untitled)'}{cancelled}: {when} \u2014 {row.get('calendar') or 'a calendar'}"]
    if row.get("location"):
        bits.append(f"at {row['location']}")
    if row.get("meeting_url"):
        bits.append(f"join: {row['meeting_url']}")
    return " ".join(bits)


def load_user_timezone_name() -> str:
    try:
        from core.user_preferences import load_user_timezone

        return str(load_user_timezone() or "")
    except Exception:
        return ""


def _handle_list_calendars(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pair, refusal = _provider_adapter_or_refusal("list calendars", read_only=True)
    if refusal is not None:
        return refusal
    _config, adapter = pair
    with _provider_scope():
        return calendar_provider.list_calendars(
            intent, task_id=task_id, session_id=session_id, adapter=adapter, audit_log_fn=audit_logger.log,
        )


def _handle_inspect_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pair, refusal = _provider_adapter_or_refusal("inspect the event", read_only=True)
    if refusal is not None:
        return refusal
    config, adapter = pair
    with _provider_scope():
        return calendar_provider.inspect_event(
            intent, task_id=task_id, session_id=session_id, config=config, adapter=adapter,
            resolve_owned_event_fn=calendar_provider.resolve_owned_provider_event,
            now_fn=_scheduling_now, audit_log_fn=audit_logger.log,
        )


def _resolve_slot_selection(text: str, *, session_id: str):
    """'option 2' -> the exact offered slot, from the session's live offer."""
    index = calendar_provider.offered_slot_number(text)
    if index is None:
        return None, None
    offer = _load_pending_action(session_id=session_id, action_kind="calendar_slot_offer")
    if offer is None:
        return None, "I don't have a live slot offer in this chat. Ask me to check the day for free slots first."
    try:
        options = json.loads(str(offer.get("scope_json") or "{}")).get("options") or []
    except json.JSONDecodeError:
        options = []
    if not 1 <= index <= len(options):
        return None, f"Option {index} is not among the {len(options)} slot(s) I offered. Pick one of those, or ask me to re-check the day."
    return options[index - 1], None


def _handle_propose_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    note_path_override: str = "",
) -> OperatorActionResult:
    pair, refusal = _provider_adapter_or_refusal("propose the event")
    if refusal is not None:
        return refusal
    config, adapter = pair
    slot, slot_problem = _resolve_slot_selection(intent.raw_text, session_id=session_id)
    note_path, note_basis = _note_action_basis(intent.raw_text)
    if note_path_override:
        # A composed request already saved its note part: the proposal links THAT file, and
        # the approval's verified receipt lands in it through the ordinary finalize path.
        note_path, note_basis = note_path_override, None
    if slot_problem and note_basis is None:
        return OperatorActionResult(ok=False, status="invalid_request", response_text=slot_problem, details={"kind": intent.kind})
    if note_basis == "missing":
        return OperatorActionResult(
            ok=False, status="invalid_request",
            response_text="I can't find that action in any note's explicit action lines ([action] ... or action: ...). Name the action as it appears in the note.",
            details={"kind": intent.kind},
        )
    if note_basis == "untimed":
        return OperatorActionResult(
            ok=False, status="invalid_request",
            response_text="That action has no time in it. Tell me when it should happen, for example: schedule the action \"call the venue\" from my note on Friday 15:00.",
            details={"kind": intent.kind},
        )

    effective_text = intent.raw_text if note_basis is None else note_basis

    def _when(text: str):
        if slot is not None:
            from core.operator.when import WhenResolution

            return WhenResolution(
                ok=True, due_at_utc=slot["start_utc"], due_wall=slot["start_local"],
                tz_name=slot.get("tz_name") or "", basis="offered slot", kind="wallclock",
            )
        return _parse_when(text)

    with _provider_scope():
        return calendar_provider.propose_event(
            intent, task_id=task_id, session_id=session_id, config=config, adapter=adapter,
            parse_when_fn=lambda _text: _when(effective_text), now_fn=_scheduling_now,
            create_pending_action_fn=_create_pending_action,
            evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
            audit_log_fn=audit_logger.log, note_path=note_path or "",
            slot_duration_minutes=_slot_minutes(slot),
        )


def _slot_minutes(slot: dict[str, Any] | None) -> int | None:
    """The chosen offered slot's own length in minutes (None when no slot was chosen)."""
    if not slot:
        return None
    try:
        start = datetime.fromisoformat(str(slot["start_utc"]))
        end = datetime.fromisoformat(str(slot["end_utc"]))
    except (KeyError, ValueError):
        return None
    minutes = int((end - start).total_seconds() // 60)
    return minutes if minutes > 0 else None


def _note_action_basis(text: str) -> tuple[str | None, str | None]:
    """A proposal sourced from a note's named action: (note_path, basis).

    basis is None when the text is not note-sourced; "missing"/"untimed" name the typed
    refusal; otherwise it is the effective text the when-authority should read (the
    action's own time clause, plus any time the user added in the same sentence).
    """
    reference = operator_notes.named_action_reference(text)
    if reference is None:
        return None, None
    named, suffix = reference
    note_path, action_line = operator_notes.find_named_action(named)
    if note_path is None:
        return None, "missing"
    title, when_text = operator_notes.split_action_time(action_line)
    # A time the user appended after the note reference ("... on Friday 15:00") extends
    # the action's own clause; the explicit user words win.
    tail = re.search(r"\b(?:at|on|in|by)\s+((?:20\d{2}-\d{2}-\d{2})|today|tomorrow|next\s+\w+|(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}(?::\d{2})?\s*(?:am|pm)).*$",
                     suffix, re.IGNORECASE)
    if tail:
        when_text = (when_text + " " + tail.group(0)).strip()
    if not when_text:
        return note_path, "untimed"
    return note_path, f'propose "{title}" {when_text}'


def _handle_provider_approval(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    """Route an explicit 'approve calendar <id>' to the provider lane action it names."""
    pair, refusal = _provider_adapter_or_refusal("execute the approved calendar action")
    if refusal is not None:
        return refusal
    config, adapter = pair
    for kind, executor in (
        ("provider_calendar_event", calendar_provider.execute_proposed_event),
        ("provider_calendar_update", calendar_provider.execute_provider_update_approval),
        ("provider_calendar_cancel", calendar_provider.execute_provider_cancel_approval),
    ):
        pending = _load_pending_action(session_id=session_id, action_kind=kind, action_id=intent.action_id)
        if pending is None:
            # Not pending: it may be an approval that was ALREADY consumed, or one whose
            # outcome is unproven — each executor diagnoses the row's actual state.
            consumed = calendar_provider.load_action_any_state(session_id=session_id, action_kind=kind, action_id=intent.action_id)
            if consumed is not None:
                gate = ExecutionGate.evaluate_local_action(
                    "schedule_calendar_event", destructive=True, user_approved=True, writes_workspace=False,
                )
                if gate.mode not in {"execute", "sandbox"}:
                    return OperatorActionResult(ok=False, status="blocked", response_text=gate.reason,
                                                details={"kind": kind, "gate_mode": gate.mode})
                with _provider_scope():
                    return executor(
                        intent, task_id=task_id, session_id=session_id, adapter=adapter,
                        load_pending_action_fn=_load_pending_action,
                        mark_action_executed_fn=_mark_action_executed,
                        audit_log_fn=audit_logger.log,
                        config=config,
                        claim_action_fn=_claim_pending_action,
                        mark_outcome_unproven_fn=_mark_action_outcome_unproven,
                        mark_effect_unrecorded_fn=_mark_action_effect_unrecorded,
                        requeue_interrupted_fn=_requeue_interrupted,
                        # A create, update or cancellation whose outcome is unproven reconciles through its
                        # recorded evidence, a verified-but-unrecorded one is re-verified (never re-sent),
                        # and an interrupted `executing` one is recovered only once its owner lock proves
                        # the owner gone; every other consumed state keeps its typed diagnosis.
                        pending_row=dict(consumed) if str(consumed.get("status")) in {"outcome_unproven", "effect_unrecorded", "executing"} else None,
                    )
            continue
        gate = ExecutionGate.evaluate_local_action(
            "schedule_calendar_event", destructive=True, user_approved=bool(intent.approval_requested) or bool(intent.action_id),
            writes_workspace=False,
        )
        if gate.mode not in {"execute", "sandbox"}:
            return OperatorActionResult(ok=False, status="blocked", response_text=gate.reason,
                                        details={"kind": kind, "gate_mode": gate.mode})
        with _provider_scope():
            return executor(
                intent, task_id=task_id, session_id=session_id, adapter=adapter,
                load_pending_action_fn=_load_pending_action,
                mark_action_executed_fn=_mark_action_executed,
                audit_log_fn=audit_logger.log,
                config=config,
                claim_action_fn=_claim_pending_action,
                mark_outcome_unproven_fn=_mark_action_outcome_unproven,
                mark_effect_unrecorded_fn=_mark_action_effect_unrecorded,
                requeue_interrupted_fn=_requeue_interrupted,
            )
    return OperatorActionResult(
        ok=False,
        status="invalid_request",
        response_text="That approval does not match a pending provider calendar action in this chat. Nothing was executed.",
        details={"action_id": intent.action_id},
    )


def _route_provider_or_draft(
    intent: OperatorActionIntent,
    *,
    session_id: str,
    provider_handler,
    honest_refusal: str,
    honest_kind: str,
):
    """Provider-first routing for move/cancel: receipt, then exact provider title, then
    drafts. Returns the chosen result, or None when the local draft path should run.
    An absent everywhere-else target is the honest typed refusal, never a guess."""
    record = calendar_provider.resolve_owned_provider_event(
        session_id=session_id, action_id=intent.action_id, label=intent.target_label)
    if record and not record.get("selection_error"):
        return provider_handler()
    if record and record.get("selection_error"):
        return OperatorActionResult(ok=False, status="invalid_request",
                                    response_text=record["selection_error"], details={"kind": honest_kind})
    if intent.target_label or intent.action_id:
        pair, refusal = _provider_adapter_or_refusal("identify the event")
        if refusal is not None:
            return refusal
        config, adapter = pair
        by_title = _title_resolver(config, adapter, series_text=intent.raw_text)(str(intent.target_label or intent.action_id or ""))
        if by_title and not by_title.get("selection_error"):
            return provider_handler()
        if by_title and by_title.get("selection_error"):
            return OperatorActionResult(ok=False, status="invalid_request",
                                        response_text=by_title["selection_error"], details={"kind": honest_kind})
    draft = _load_executed_calendar_draft(session_id=session_id, action_id=intent.action_id, target_label=intent.target_label)
    if draft is None or draft.get("selection_error"):
        return OperatorActionResult(
            ok=False, status="invalid_request",
            response_text=(draft or {}).get("selection_error") or honest_refusal,
            details={"kind": honest_kind},
        )
    return None


def _title_resolver(config: Any, adapter: Any, *, now_fn: Any = None, series_text: str = ""):
    """Resolve an update/cancel target by EXACT provider title (post-restart path).

    Returns a receipt-shaped record carrying the provider's CURRENT etag — the version
    an inspect just showed — or None/selection_error exactly like the receipt resolver.
    """

    def resolve(label: str):
        with _provider_scope():
            try:
                calendar_id, _name = calendar_provider.resolve_calendar(config, adapter, named=None)
                matching = calendar_provider._events_titled(adapter, calendar_id, label, now_fn=now_fn or _scheduling_now)
            except CalendarRefusedError as exc:
                return {"selection_error": _title_resolver_refusal(exc)}
            if not matching:
                return None
            if len(matching) != 1 and series_text:
                dated, _note = calendar_provider._named_occurrence(series_text, matching)
                if len(dated) == 1 and dated[0] is not matching[0]:
                    matching = dated
            if len(matching) != 1 and series_text and calendar_provider._names_series_scope(series_text):
                # A request naming the SERIES resolves same-title expanded occurrences to their
                # one master, which is the object a series action edits.
                series_uids = {str(event.series_id or event.uid) for event in matching}
                if len(series_uids) == 1:
                    with _provider_scope():
                        master = adapter.get_event(calendar_id, series_uids.pop())
                    matching = [master]
            if len(matching) != 1:
                choices = ", ".join(f"{event.summary} (uid {event.uid[:8]})" for event in matching[:5])
                return {"selection_error": f"Several provider events match {label!r}. Tell me which one: {choices}"}
            event = matching[0]
            return {
                "action_id": "",
                "uid": event.uid,
                "etag": event.etag,
                "href": event.href,
                "title": event.summary,
                "start_utc": event.start_utc,
                "end_utc": event.end_utc,
                "calendar_id": event.calendar_id,
                "note_path": "",
                "series_id": str(getattr(event, "series_id", "") or ""),
                "original_start": str(getattr(event, "original_start", "") or ""),
            }

    return resolve


def _title_resolver_refusal(exc: CalendarRefusedError) -> str:
    if exc.reason == "calendar_choice_required":
        return f"Several calendars are configured on this provider — name which one. {exc.detail}"
    return f"The calendar provider refused the lookup ({exc.reason}). Nothing was changed."


def _handle_provider_update(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pair, refusal = _provider_adapter_or_refusal("update the event")
    if refusal is not None:
        return refusal
    config, adapter = pair
    return calendar_provider.stage_provider_update(
        intent, task_id=task_id, session_id=session_id,
        resolve_owned_event_fn=calendar_provider.resolve_owned_provider_event,
        parse_when_fn=_parse_when, create_pending_action_fn=_create_pending_action,
        resolve_by_title_fn=_title_resolver(config, adapter, series_text=intent.raw_text),
        provider_binding={"provider": config.provider, "provider_url": config.base_url, "auth_binding": config.auth_binding},
    )


def _handle_provider_cancel(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pair, refusal = _provider_adapter_or_refusal("cancel the event")
    if refusal is not None:
        return refusal
    config, adapter = pair
    return calendar_provider.stage_provider_cancel(
        intent, task_id=task_id, session_id=session_id,
        resolve_owned_event_fn=calendar_provider.resolve_owned_provider_event,
        create_pending_action_fn=_create_pending_action,
        resolve_by_title_fn=_title_resolver(config, adapter, series_text=intent.raw_text),
        provider_binding={"provider": config.provider, "provider_url": config.base_url, "auth_binding": config.auth_binding},
    )


def _handle_update_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    if not _provider_configured():
        return calendar_provider.missing_configuration_result("rename or retitle an event")
    return _handle_provider_update(intent, task_id=task_id, session_id=session_id)


def _handle_save_note(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_notes.handle_save_note(
        intent, task_id=task_id, session_id=session_id,
        evaluate_local_action_fn=ExecutionGate.evaluate_local_action,
        audit_log_fn=audit_logger.log,
    )


def _handle_find_notes(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_notes.handle_find_notes(intent, task_id=task_id, session_id=session_id, audit_log_fn=audit_logger.log)


def _handle_show_note(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_notes.handle_show_note(intent, task_id=task_id, session_id=session_id, audit_log_fn=audit_logger.log)


def _handle_move_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    return operator_handlers.handle_move_calendar_event(
        intent,
        task_id=task_id,
        session_id=session_id,
        parse_when_fn=_parse_when,
        load_executed_calendar_fn=_load_executed_calendar_draft,
        move_calendar_artifact_fn=_move_calendar_artifact,
        audit_log_fn=audit_logger.log,
    )


def _scheduling_now():
    from core.time_authority import CLOCK
    from core.user_preferences import load_user_timezone
    return CLOCK.now_for_timezone(load_user_timezone() or 'UTC')


def _parse_when(text: str):
    return operator_when.parse_when_expression(
        text,
        now_fn=_scheduling_now,
    )


def _schedule_reminder(**kwargs):
    return operator_reminders.schedule_reminder(get_connection_fn=get_connection, **kwargs)


def _list_reminders(**kwargs):
    return operator_reminders.list_reminders(get_connection_fn=get_connection, **kwargs)


def _load_reminder(reminder_id: str):
    return operator_reminders.load_reminder(reminder_id, get_connection_fn=get_connection)


def _cancel_reminder(reminder_id: str):
    return operator_reminders.cancel_reminder(reminder_id, get_connection_fn=get_connection)


def _move_reminder(reminder_id: str, **kwargs):
    return operator_reminders.move_reminder(reminder_id, get_connection_fn=get_connection, **kwargs)


def _complete_reminder(reminder_id: str):
    return operator_reminders.complete_reminder(reminder_id, get_connection_fn=get_connection)


def _edit_reminder(reminder_id: str, **kwargs):
    return operator_reminders.edit_reminder(reminder_id, get_connection_fn=get_connection, **kwargs)


def _load_executed_calendar_draft(*, session_id: str, action_id: str | None = None,
                                  target_label: str | None = None) -> dict[str, Any] | None:
    """Resolve exactly one owned draft; never guess from creation order."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT action_id, result_json FROM operator_action_requests "
            "WHERE session_id = ? AND action_kind = 'schedule_calendar_event' AND status = 'executed' "
            "AND (? IS NULL OR action_id LIKE ?) ORDER BY created_at DESC LIMIT 201",
            (session_id, action_id, (action_id or '') + '%'),
        ).fetchall()
    finally:
        conn.close()
    candidates = []
    for row in rows:
        try:
            result = json.loads(str(row['result_json'] or '{}'))
        except json.JSONDecodeError:
            continue
        path = str(result.get('ics_path') or '')
        if not path or not Path(path).expanduser().is_file():
            continue
        if target_label and str(result.get('title') or '').casefold() != target_label.casefold():
            continue
        candidates.append({'action_id': str(row['action_id']), 'source_sha256': hashlib.sha256(Path(path).expanduser().read_bytes()).hexdigest(), **{
            key: str(result.get(key) or '') for key in ('title', 'start_iso', 'end_iso', 'ics_path')
        }})
    if len(candidates) == 1 and len(rows) <= 200:
        return candidates[0]
    if not candidates:
        return None
    choices = ', '.join(f"{r['title']} ({r['action_id']})" for r in candidates[:8])
    return {'selection_error': 'Choose one calendar draft by its exact title or ID: ' + choices}


def _cancel_calendar_artifact(record: dict[str, Any]) -> dict[str, Any]:
    return _mutate_calendar_artifact(record)


def _move_calendar_artifact(record: dict[str, Any], when: Any) -> dict[str, Any]:
    return _mutate_calendar_artifact(record, when=when)


def _mutate_calendar_artifact(record: dict[str, Any], *, when: Any = None) -> dict[str, Any]:
    """Serialize local draft edits and reject stale selections before touching the file."""
    ics_path = Path(str(record.get('ics_path') or '')).expanduser().resolve()
    if not ics_path.is_relative_to(data_path('calendar_outbox').resolve()) or ics_path.suffix != '.ics':
        return {'ok': False, 'status': 'artifact_scope_mismatch', 'reason': 'the draft is outside the local calendar outbox'}
    conn = get_connection()
    changed = False
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT status, result_json FROM operator_action_requests WHERE action_id = ?",
                           (str(record.get('action_id') or ''),)).fetchone()
        saved = json.loads(str(row['result_json'] or '{}')) if row else {}
        if (not row or row['status'] != 'executed'
                or any(str(saved.get(key) or '') != str(record.get(key) or '')
                       for key in ('title', 'start_iso', 'end_iso', 'ics_path'))):
            return {'ok': False, 'status': 'stale_draft', 'reason': 'the draft changed after selection; select its current version'}
        original_bytes = ics_path.read_bytes()
        if record.get('source_sha256') and hashlib.sha256(original_bytes).hexdigest() != record['source_sha256']:
            return {'ok': False, 'status': 'stale_draft', 'reason': 'the calendar file changed after selection'}
        if when is None:
            ics_path.unlink()
            changed = True
            conn.execute("UPDATE operator_action_requests SET status = 'cancelled', updated_at = ? WHERE action_id = ?",
                         (_utcnow(), record['action_id']))
            result = {'ok': True, 'status': 'cancelled', 'ics_path': str(ics_path), 'artifact_removed': True}
        else:
            start = datetime.fromisoformat(str(when.due_at_utc))
            duration = datetime.fromisoformat(saved['end_iso']) - datetime.fromisoformat(saved['start_iso'])
            if duration <= timedelta(0):
                raise ValueError('the saved draft has no valid duration')
            end = start + duration
            updated = original_bytes.decode('utf-8')
            for name, value in (('DTSTART', start), ('DTEND', end)):
                updated, count = re.subn(rf'(?m)^{name}:[^\r\n]*', f'{name}:{value.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}', updated)
                if count != 1:
                    raise ValueError('the draft no longer matches the single-event artifact')
            from core.execution.artifacts import atomic_write_text
            atomic_write_text(ics_path, updated)
            changed = True
            saved.update(start_iso=when.due_at_utc, end_iso=end.isoformat(), moved_at=_utcnow())
            conn.execute('UPDATE operator_action_requests SET result_json = ?, updated_at = ? WHERE action_id = ?',
                         (json.dumps(saved, sort_keys=True), _utcnow(), record['action_id']))
            result = {'ok': True, 'status': 'moved', 'ics_path': str(ics_path), 'new_start': when.due_at_utc}
        conn.commit()
        return result
    except (OSError, ValueError, sqlite3.Error) as exc:
        if changed:
            return {'ok': False, 'status': 'artifact_state_uncertain',
                    'reason': 'the local file changed but its state receipt could not be saved; inspect the draft before retrying'}
        return {'ok': False, 'status': 'artifact_error', 'reason': str(exc)}
    finally:
        conn.close()


def _resolve_target_path(raw_path: str | None) -> Path:
    return operator_storage.resolve_target_path(
        raw_path,
        os_name=os.name,
        env=os.environ,
        home_dir_fn=Path.home,
    )


def _inspect_storage(target: Path) -> dict[str, Any]:
    return operator_storage.inspect_storage(
        target,
        disk_usage_fn=shutil.disk_usage,
        path_size_fn=_path_size,
        monotonic_fn=time.monotonic,
    )


def _path_size(path: Path, *, deadline: float, max_entries: int = 6000) -> dict[str, Any]:
    return operator_storage.path_size(
        path,
        deadline=deadline,
        max_entries=max_entries,
        walk_fn=os.walk,
        monotonic_fn=time.monotonic,
    )


def _inspect_processes() -> list[dict[str, Any]]:
    return operator_system.inspect_processes(
        os_name=os.name,
        subprocess_run=subprocess.run,
        csv_module=csv,
    )


def _inspect_services() -> list[dict[str, Any]]:
    return operator_system.inspect_services(
        os_name=os.name,
        subprocess_run=subprocess.run,
        which_fn=shutil.which,
    )


def _parse_calendar_request(text: str) -> dict[str, Any] | None:
    return operator_calendar.parse_calendar_request(
        text,
        extract_quoted_values_fn=_extract_quoted_values,
        data_path_fn=data_path,
        now_fn=_scheduling_now,
    )


def _render_ics(*, title: str, start_iso: str, end_iso: str) -> str:
    return operator_calendar.render_ics(
        title=title,
        start_iso=start_iso,
        end_iso=end_iso,
        uuid_fn=uuid.uuid4,
        now_fn=lambda: datetime.now(timezone.utc),
    )


def _parse_move_request(
    text: str,
    *,
    fallback_source: str | None = None,
    fallback_destination: str | None = None,
) -> dict[str, str] | None:
    return operator_storage.parse_move_request(
        text,
        fallback_source=fallback_source,
        fallback_destination=fallback_destination,
        extract_quoted_values_fn=_extract_quoted_values,
        data_path_fn=data_path,
        expandvars_fn=os.path.expandvars,
    )


def _candidate_cleanup_roots(target_path: str | None) -> list[Path]:
    return operator_storage.candidate_cleanup_roots(
        target_path,
        env=os.environ,
        gettempdir_fn=tempfile.gettempdir,
        is_temp_cleanup_path_fn=_is_temp_cleanup_path,
        expandvars_fn=os.path.expandvars,
    )


def _is_temp_cleanup_path(path: Path) -> bool:
    return operator_storage.is_temp_cleanup_path(
        path,
        path_is_denied_fn=_path_is_denied,
        tempish_names=_TEMPISH_NAMES,
        gettempdir_fn=tempfile.gettempdir,
        home_dir_fn=Path.home,
        is_relative_to_fn=_is_relative_to,
    )


def _operator_safe_path(path: Path) -> bool:
    return operator_storage.operator_safe_path(
        path,
        path_is_denied_fn=_path_is_denied,
        gettempdir_fn=tempfile.gettempdir,
        data_path_fn=data_path,
        is_relative_to_fn=_is_relative_to,
        home_dir_fn=Path.home,
    )


def _validate_move_scope(source: Path, destination_dir: Path) -> str | None:
    return operator_storage.validate_move_scope(
        source,
        destination_dir,
        operator_safe_path_fn=_operator_safe_path,
        resolved_move_target_fn=_resolved_move_target,
        is_relative_to_fn=_is_relative_to,
    )


def _resolved_move_target(source: Path, destination_dir: Path) -> Path:
    return operator_storage.resolved_move_target(source, destination_dir)


def _path_is_denied(path: Path) -> bool:
    return operator_storage.path_is_denied(path, policy_get=policy_engine.get)


def _delete_children(root: Path) -> dict[str, Any]:
    return operator_storage.delete_children(root, rmtree_fn=shutil.rmtree)


def _create_pending_action(*, session_id: str, task_id: str, action_kind: str, scope: dict[str, Any]) -> str:
    return operator_approvals.create_pending_action(
        session_id=session_id,
        task_id=task_id,
        action_kind=action_kind,
        scope=scope,
        now_fn=_utcnow,
        get_connection_fn=get_connection,
    )


def _load_pending_action(*, session_id: str, action_kind: str, action_id: str | None = None) -> dict[str, Any] | None:
    return operator_approvals.load_pending_action(
        session_id=session_id,
        action_kind=action_kind,
        action_id=action_id,
        get_connection_fn=get_connection,
    )


def _mark_action_executed(action_id: str, *, result: dict[str, Any]) -> None:
    operator_approvals.mark_action_executed(
        action_id,
        result=result,
        now_fn=_utcnow,
        get_connection_fn=get_connection,
    )


def _claim_pending_action(action_id: str) -> bool:
    return operator_approvals.claim_pending_action(
        action_id,
        now_fn=_utcnow,
        get_connection_fn=get_connection,
    )


def _mark_action_outcome_unproven(action_id: str, *, result: dict[str, Any]) -> None:
    operator_approvals.mark_action_outcome_unproven(
        action_id,
        result=result,
        now_fn=_utcnow,
        get_connection_fn=get_connection,
    )


def _mark_action_effect_unrecorded(action_id: str, *, result: dict[str, Any]) -> None:
    operator_approvals.mark_action_effect_unrecorded(
        action_id,
        result=result,
        now_fn=_utcnow,
        get_connection_fn=get_connection,
    )


def _requeue_interrupted(action_id: str, *, entry_state: str, note: str) -> str | bool:
    return operator_approvals.requeue_interrupted_action(
        action_id,
        entry_state=entry_state,
        note=note,
        now_fn=_utcnow,
        get_connection_fn=get_connection,
    )


def _fmt_bytes(value: int) -> str:
    return operator_storage.fmt_bytes(value)


def _is_relative_to(path: Path, base: Path) -> bool:
    return operator_storage.is_relative_to(path, base)


def _utcnow() -> str:
    return operator_approvals.utcnow()

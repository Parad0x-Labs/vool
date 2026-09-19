"""Calendar accounts and the calendars chosen on them: the setup authority the sync, the agenda and alerts read.

An account is one connection to one calendar source: a Google or Microsoft 365 account reached through a
credential binding, an iCloud or other CalDAV server, or Apple Calendar on this Mac through EventKit. The
account names its credential by BINDING id only. The KAS transport resolves and attaches the secret, so no
token is stored, logged or returned here.

Law:

* Nothing is fetched or scheduled until the user opts in: a new account has sync and alerts off.
* Calendars are discovered from the provider, then chosen. Only chosen calendars are read, and at most one
  calendar is the default write target.
* Two accounts never cross: every calendar, projection and alert is keyed by its account.
* The account's state is what the last attempt proved (connected, revoked, denied, rate limited, offline,
  unreadable, unsupported, disconnected), each with a recovery step. "Stale" is derived from the age of the last
  good sync when the account is shown, never stored as a guess.
* Disconnecting stops future fetches and cancels that account's pending alerts. It cannot retract an alert
  already shown.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

DISCOVERY_SCOPE_NAME = "calendar.account_discovery"

#: The calendar sources VOOL can connect, with the capability each path has. Settings shows these as written.
PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "label": "Google Calendar",
        "connection": "a Google account credential binding (OAuth)",
        "events": "recurring series are read as the dated instances Google expands",
        "links": "the event page; Google Meet and conference links when the event has them",
    },
    "graph": {
        "label": "Microsoft Outlook / Microsoft 365",
        "connection": "a Microsoft account credential binding (OAuth)",
        "events": "recurring series are read as the dated occurrences calendarView expands",
        "links": "the event page; Teams and online meeting join links when the event has them",
    },
    "caldav": {
        "label": "iCloud or another CalDAV server",
        "connection": "the server's CalDAV address and a credential binding (for iCloud, an app-specific password)",
        "events": "stored events are read as the server returns them; servers differ in how they expand recurring series",
        "links": "the event's URL property when the server keeps one",
    },
    "eventkit": {
        "label": "Apple Calendar on this Mac",
        "connection": "macOS Calendar permission for VOOL, granted in the system prompt (no password)",
        "events": "the calendars macOS already syncs are read, recurring instances included",
        "links": "the event's URL when it has one",
    },
}

#: What to do next for each state an attempt can prove.
RECOVERY: dict[str, str] = {
    "configured": "Refresh the calendar list, choose calendars, then turn on sync.",
    "connected": "",
    "revoked": "The provider no longer accepts this account's credential. Reconnect the account in Settings; "
               "VOOL keeps the alerts it already scheduled but cannot see changes until then.",
    "denied": "The provider refused access to this calendar. Check the account's calendar permission, then reconnect it in Settings.",
    "rate_limited": "The provider asked VOOL to slow down. VOOL tries again later on its own.",
    "offline": "The provider could not be reached. VOOL retries on its own; check the network or the server address.",
    "egress_refused": "This VOOL keeps network access off for this request (local-only mode or a permission rule). "
                      "Change that setting to sync this account.",
    "unreadable": "The provider answered with data VOOL could not read as a calendar. VOOL kept the alerts it had; "
                  "try again later or reconnect the account.",
    "calendar_missing": "A chosen calendar is no longer on the account. Refresh the calendar list in Settings.",
    "unsupported": "This calendar source cannot be used on this machine as configured.",
    "provider_error": "The provider returned an error. VOOL retries on its own.",
    "stale": "VOOL has not refreshed this account recently, so recent changes may be missing.",
    "disconnected": "Disconnected. Reconnect the account in Settings to fetch events and schedule alerts again.",
}


def _utcnow() -> str:
    from core.time_authority import CLOCK

    return CLOCK.now_utc().isoformat()


def _default_connection():
    from storage.db import get_connection

    return get_connection()


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def decode_minutes(raw: Any, *, default: list[int] | None) -> list[int] | None:
    """A stored lead-time list, or ``default`` when nothing valid is stored."""
    if raw is None or raw == "":
        return default
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
    if not isinstance(value, list) or any(isinstance(part, bool) or not isinstance(part, int) for part in value):
        return default
    return [int(part) for part in value]


def _account(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["sync_enabled"] = bool(data.get("sync_enabled"))
    data["alerts_enabled"] = bool(data.get("alerts_enabled"))
    data["default_lead_minutes"] = decode_minutes(data.pop("default_lead_minutes_json", None), default=[15])
    data["recovery"] = RECOVERY.get(str(data.get("status") or ""), "")
    return data


def add_account(
    *,
    provider: str,
    base_url: str = "",
    label: str = "",
    auth_binding: str = "",
    principal: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Register a connection. Adding the same provider, address, binding and principal again returns the same
    account, so a repeated setup never creates a duplicate source."""
    name = str(provider or "").strip().lower()
    if name not in PROVIDERS:
        return {"ok": False, "reason": "unsupported_provider", "detail": f"no calendar source named {name or 'nothing'}"}
    base = str(base_url or "").strip().rstrip("/")
    binding = str(auth_binding or "").strip()
    if name == "caldav" and not base:
        return {"ok": False, "reason": "base_url_required", "detail": "a CalDAV account needs its server address"}
    if name == "eventkit" and not base:
        base = "eventkit://local"
    if binding:
        refusal = _binding_refusal(binding)
        if refusal is not None:
            return refusal
    who = str(principal or "").strip()
    identity = hashlib.sha256("\0".join([name, base, binding, who]).encode("utf-8")).hexdigest()
    now = now_fn()
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO calendar_accounts (
                account_id, identity_key, provider, label, base_url, auth_binding, principal, status, status_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'configured', ?, ?, ?)
            """,
            ("cal-" + uuid.uuid4().hex[:16], identity, name, (str(label or "").strip() or PROVIDERS[name]["label"])[:120],
             base, binding, who, now, now, now),
        )
        created = cursor.rowcount == 1
        conn.commit()
        row = conn.execute("SELECT * FROM calendar_accounts WHERE identity_key = ?", (identity,)).fetchone()
    finally:
        conn.close()
    account = _account(row)
    account.update(ok=True, created=created)
    return account


def _binding_refusal(binding: str) -> dict[str, Any] | None:
    """Why an account cannot name this credential binding, or None when it can (it exists and is verified)."""
    from core.credential_intelligence.binding import MalformedBindingIndexError, load_index

    try:
        rows = load_index()
    except MalformedBindingIndexError:
        return {"ok": False, "reason": "credential_binding_index_unreadable", "detail": "the credential binding list could not be read"}
    row = rows.get(binding) or next((entry for entry in rows.values() if str(entry.get("binding_id") or "") == binding), None)
    if row is None:
        return {"ok": False, "reason": "unknown_credential_binding", "detail": f"no credential binding {binding} exists"}
    if str(row.get("status") or "") != "verified":
        return {"ok": False, "reason": "credential_binding_not_verified",
                "detail": f"credential binding {binding} is {row.get('status') or 'unverified'}; verify it under Keys first"}
    return None


def list_credential_bindings() -> list[dict[str, Any]]:
    """The credential bindings an account may name: id, provider, account and status only, never a secret."""
    from core.credential_intelligence.binding import binding_from_row, load_index

    bindings = [binding.to_dict() for binding in (binding_from_row(row) for row in load_index().values()) if binding is not None]
    return sorted(bindings, key=lambda entry: (entry["status"] != "verified", str(entry["binding_id"])))


def load_account(account_id: str, *, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any] | None:
    conn = get_connection_fn()
    try:
        row = conn.execute("SELECT * FROM calendar_accounts WHERE account_id = ?", (str(account_id or ""),)).fetchone()
    finally:
        conn.close()
    return _account(row) if row is not None else None


def list_accounts(*, get_connection_fn: Callable[[], Any] = _default_connection) -> list[dict[str, Any]]:
    conn = get_connection_fn()
    try:
        rows = conn.execute("SELECT * FROM calendar_accounts ORDER BY created_at ASC").fetchall()
    finally:
        conn.close()
    return [_account(row) for row in rows]


def present_account(account: dict[str, Any], *, now_iso: str, stale_after_minutes: int) -> dict[str, Any]:
    """The account as Settings and the API show it: no credential binding value, derived staleness."""
    view = {key: value for key, value in account.items() if key not in {"auth_binding", "identity_key", "ok", "created"}}
    view["has_credential_binding"] = bool(account.get("auth_binding"))
    view["credential_binding"] = str(account.get("auth_binding") or "")  # the binding id; the secret stays in the credential store
    view["capability"] = dict(PROVIDERS.get(str(account.get("provider") or ""), {}))
    status = str(account.get("status") or "")
    if status == "connected" and account.get("sync_enabled"):
        last_ok, now = _parse(account.get("last_sync_ok_at")), _parse(now_iso)
        if last_ok is not None and now is not None and now - last_ok > timedelta(minutes=stale_after_minutes):
            status = "stale"
    view["status"] = status
    view["recovery"] = RECOVERY.get(status, "")
    return view


def _selection(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["selected"] = bool(data.get("selected"))
    data["present"] = bool(data.get("present"))
    data["is_default_write"] = bool(data.get("is_default_write"))
    data["provider_default"] = bool(data.get("provider_default"))
    data["can_write"] = None if data.get("can_write") is None else bool(data.get("can_write"))
    data["alert_lead_minutes"] = decode_minutes(data.pop("alert_lead_minutes_json", None), default=None)
    return data


def list_selections(account_id: str, *, get_connection_fn: Callable[[], Any] = _default_connection) -> list[dict[str, Any]]:
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            "SELECT * FROM calendar_selections WHERE account_id = ? ORDER BY display_name COLLATE NOCASE ASC",
            (str(account_id or ""),),
        ).fetchall()
    finally:
        conn.close()
    return [_selection(row) for row in rows]


def build_adapter(account: dict[str, Any]) -> Any:
    """The account's calendar adapter, built by the one registry factory with the account's own binding."""
    from core.kas.registry import calendar_adapter

    return calendar_adapter(
        str(account["provider"]),
        base_url=str(account.get("base_url") or ""),
        auth_binding=str(account.get("auth_binding") or ""),
        options={"timeout": "20"},
    )


def classify_calendar_error(exc: BaseException) -> tuple[str, str]:
    """(state, short detail) for a failed provider attempt. The detail never carries a credential."""
    from core.kas.contract import (
        CalendarReadUnusableError,
        CalendarRefusedError,
        TransportDeniedError,
        TransportUnknownError,
    )

    if isinstance(exc, CalendarReadUnusableError):
        return "unreadable", str(exc.detail or "unreadable calendar data")[:200]
    if isinstance(exc, CalendarRefusedError):
        if exc.reason in {"os_binding_unavailable", "unsupported"}:
            return "unsupported", str(exc.detail or exc.reason)[:200]
        if exc.status_code == 401:
            return "revoked", "the provider answered HTTP 401"
        if exc.status_code == 403:
            return "denied", "the provider answered HTTP 403"
        if exc.status_code == 429 or exc.reason == "rate_limited":
            return "rate_limited", "the provider answered HTTP 429"
        if exc.reason == "not_found":
            return "calendar_missing", "the provider answered not found"
        return "provider_error", f"the provider answered HTTP {exc.status_code}"
    if isinstance(exc, TransportDeniedError):
        if exc.reason == "egress_denied":
            return "egress_refused", "the outbound request was refused before it left this machine"
        return "offline", f"the provider could not be reached ({exc.reason})"
    if isinstance(exc, TransportUnknownError):
        return "offline", f"no reply arrived from the provider ({exc.reason})"
    try:
        from core.remote_fetch_policy import RemoteFetchRefusedError

        if isinstance(exc, RemoteFetchRefusedError):
            return "egress_refused", "the outbound request was refused before it left this machine"
    except Exception:
        pass
    if isinstance(exc, (LookupError, ValueError)):
        return "unsupported", str(exc)[:200]
    return "provider_error", type(exc).__name__


def _set_status(account_id: str, *, status: str, detail: str, now: str, get_connection_fn: Callable[[], Any]) -> None:
    conn = get_connection_fn()
    try:
        conn.execute(
            "UPDATE calendar_accounts SET status = ?, status_detail = ?, status_at = ?, updated_at = ? WHERE account_id = ? AND status != 'disconnected'",
            (status, str(detail or "")[:300], now, now, account_id),
        )
        conn.commit()
    finally:
        conn.close()


def discover_calendars(
    account_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
    adapter_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Read the account's calendars from the provider and record them. Existing choices are kept."""
    account = load_account(account_id, get_connection_fn=get_connection_fn)
    if account is None:
        return {"ok": False, "status": "unknown_account", "calendars": []}
    if account["status"] == "disconnected":
        return {"ok": False, "status": "disconnected", "recovery": RECOVERY["disconnected"], "calendars": []}
    now = now_fn()
    from core.effect_gateway import named_background_effect_scope

    try:
        with named_background_effect_scope(DISCOVERY_SCOPE_NAME):
            adapter = (adapter_factory or build_adapter)(account)
            calendars = list(adapter.list_calendars())
    except Exception as exc:
        status, detail = classify_calendar_error(exc)
        _set_status(account_id, status=status, detail=detail, now=now, get_connection_fn=get_connection_fn)
        return {"ok": False, "status": status, "detail": detail, "recovery": RECOVERY.get(status, ""),
                "calendars": list_selections(account_id, get_connection_fn=get_connection_fn)}
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE calendar_selections SET present = 0 WHERE account_id = ?", (account_id,))
        for calendar in calendars:
            stated = getattr(calendar, "can_write", None)
            conn.execute(
                """
                INSERT INTO calendar_selections (account_id, calendar_id, display_name, can_write, provider_default, present, discovered_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(account_id, calendar_id) DO UPDATE SET
                    display_name = excluded.display_name, can_write = excluded.can_write,
                    provider_default = excluded.provider_default, present = 1, updated_at = excluded.updated_at
                """,
                (account_id, str(calendar.calendar_id), str(calendar.display_name or calendar.calendar_id)[:160],
                 None if stated is None else (1 if stated else 0), 1 if getattr(calendar, "provider_default", False) is True else 0, now, now),
            )
        # A default write calendar that became read-only or disappeared is no longer the default.
        conn.execute(
            "UPDATE calendar_selections SET is_default_write = 0, updated_at = ? WHERE account_id = ? AND is_default_write = 1 AND (can_write = 0 OR present = 0)",
            (now, account_id),
        )
        conn.execute(
            "UPDATE calendar_accounts SET status = 'connected', status_detail = '', status_at = ?, updated_at = ? WHERE account_id = ?",
            (now, now, account_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "status": "connected", "calendars": list_selections(account_id, get_connection_fn=get_connection_fn)}


def select_calendar(
    account_id: str,
    calendar_id: str,
    *,
    selected: bool,
    default_write: bool | None = None,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Choose (or stop reading) one discovered calendar; optionally make it the one default write calendar."""
    now = now_fn()
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT present, can_write FROM calendar_selections WHERE account_id = ? AND calendar_id = ?", (account_id, calendar_id)
        ).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "unknown_calendar", "detail": "refresh the account's calendar list first"}
        if default_write is True and row["can_write"] is not None and not row["can_write"]:
            conn.rollback()
            return {"ok": False, "reason": "calendar_read_only",
                    "detail": "the provider says this calendar is read-only for this account; choose a calendar you can write to"}
        if default_write is True and not row["present"]:
            conn.rollback()
            return {"ok": False, "reason": "calendar_missing", "detail": RECOVERY.get("calendar_missing", "")}
        conn.execute(
            "UPDATE calendar_selections SET selected = ?, updated_at = ? WHERE account_id = ? AND calendar_id = ?",
            (1 if selected else 0, now, account_id, calendar_id),
        )
        if default_write is True:
            conn.execute("UPDATE calendar_selections SET is_default_write = 0, updated_at = ? WHERE is_default_write = 1", (now,))
            conn.execute(
                "UPDATE calendar_selections SET is_default_write = 1, updated_at = ? WHERE account_id = ? AND calendar_id = ?",
                (now, account_id, calendar_id),
            )
        elif default_write is False:
            conn.execute(
                "UPDATE calendar_selections SET is_default_write = 0, updated_at = ? WHERE account_id = ? AND calendar_id = ?",
                (now, account_id, calendar_id),
            )
        conn.commit()
    finally:
        conn.close()
    cancelled = 0
    if not selected:
        from core.operator import calendar_alerts

        cancelled = calendar_alerts.cancel_calendar_alerts(
            account_id=account_id, calendar_id=calendar_id, reason="the calendar is no longer chosen",
            now_fn=now_fn, get_connection_fn=get_connection_fn,
        )
    return {"ok": True, "account_id": account_id, "calendar_id": calendar_id, "selected": bool(selected), "alerts_cancelled": cancelled}


def set_opt_in(
    account_id: str,
    *,
    sync_enabled: bool | None = None,
    alerts_enabled: bool | None = None,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Turn sync and alerts on or off. Turning alerts off cancels the account's pending alerts."""
    account = load_account(account_id, get_connection_fn=get_connection_fn)
    if account is None:
        return {"ok": False, "reason": "unknown_account"}
    if account["status"] == "disconnected" and (sync_enabled or alerts_enabled):
        return {"ok": False, "reason": "disconnected", "recovery": RECOVERY["disconnected"]}
    now = now_fn()
    assignments, params = ["updated_at = ?"], [now]
    if sync_enabled is not None:
        assignments.append("sync_enabled = ?")
        params.append(1 if sync_enabled else 0)
        if sync_enabled and not account.get("next_sync_due_at"):
            assignments.append("next_sync_due_at = ?")
            params.append(now)
    if alerts_enabled is not None:
        assignments.append("alerts_enabled = ?")
        params.append(1 if alerts_enabled else 0)
    conn = get_connection_fn()
    try:
        conn.execute(f"UPDATE calendar_accounts SET {', '.join(assignments)} WHERE account_id = ?", [*params, account_id])
        conn.commit()
    finally:
        conn.close()
    cancelled = 0
    if alerts_enabled is False:
        from core.operator import calendar_alerts

        cancelled = calendar_alerts.cancel_calendar_alerts(
            account_id=account_id, reason="alerts turned off for this account", now_fn=now_fn, get_connection_fn=get_connection_fn,
        )
    result = load_account(account_id, get_connection_fn=get_connection_fn) or {}
    result.update(ok=True, alerts_cancelled=cancelled)
    return result


def record_sync_result(
    account_id: str,
    *,
    ok: bool,
    status: str,
    detail: str,
    started_at: str,
    next_due_at: str,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> None:
    conn = get_connection_fn()
    try:
        if ok:
            conn.execute(
                """
                UPDATE calendar_accounts
                SET status = 'connected', status_detail = '', status_at = ?, last_sync_started_at = ?, last_sync_ok_at = ?,
                    next_sync_due_at = ?, sync_failures = 0, updated_at = ?
                WHERE account_id = ? AND status != 'disconnected'
                """,
                (started_at, started_at, started_at, next_due_at, started_at, account_id),
            )
        else:
            conn.execute(
                """
                UPDATE calendar_accounts
                SET status = ?, status_detail = ?, status_at = ?, last_sync_started_at = ?, next_sync_due_at = ?,
                    sync_failures = sync_failures + 1, updated_at = ?
                WHERE account_id = ? AND status != 'disconnected'
                """,
                (status, str(detail or "")[:300], started_at, started_at, next_due_at, started_at, account_id),
            )
        conn.commit()
    finally:
        conn.close()


def disconnect_account(
    account_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Stop fetching this account and cancel its pending alerts. Already shown alerts stay in history."""
    account = load_account(account_id, get_connection_fn=get_connection_fn)
    if account is None:
        return {"ok": False, "reason": "unknown_account"}
    now = now_fn()
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            UPDATE calendar_accounts
            SET status = 'disconnected', status_detail = '', status_at = ?, sync_enabled = 0, alerts_enabled = 0,
                disconnected_at = ?, next_sync_due_at = '', updated_at = ?
            WHERE account_id = ?
            """,
            (now, now, now, account_id),
        )
        conn.commit()
    finally:
        conn.close()
    from core.operator import calendar_alerts

    cancelled = calendar_alerts.cancel_calendar_alerts(
        account_id=account_id, reason="account disconnected", now_fn=now_fn, get_connection_fn=get_connection_fn,
    )
    conn = get_connection_fn()
    try:
        conn.execute(
            "UPDATE calendar_event_projections SET state = 'disconnected', updated_at = ? WHERE account_id = ? AND state = 'active'",
            (now, account_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "account_id": account_id, "status": "disconnected", "alerts_cancelled": cancelled}


def reconnect_account(
    account_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Bring a disconnected account back to ``configured``. Sync and alerts stay off until the user opts in again."""
    now = now_fn()
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE calendar_accounts SET status = 'configured', status_detail = '', status_at = ?, disconnected_at = NULL, updated_at = ?
            WHERE account_id = ? AND status = 'disconnected'
            """,
            (now, now, account_id),
        )
        conn.commit()
    finally:
        conn.close()
    if cursor.rowcount != 1:
        return {"ok": False, "reason": "not_disconnected"}
    result = load_account(account_id, get_connection_fn=get_connection_fn) or {}
    result.update(ok=True)
    return result


def presented(account: dict[str, Any], *, now_fn: Callable[[], str] = _utcnow,
              get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """One account as Settings shows it, with its calendars."""
    from core.operator.calendar_alerts import STALE_AFTER_MINUTES

    view = present_account(account, now_iso=now_fn(), stale_after_minutes=STALE_AFTER_MINUTES)
    view["calendars"] = list_selections(str(account["account_id"]), get_connection_fn=get_connection_fn)
    return view


def settings_view(*, now_fn: Callable[[], str] = _utcnow, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """What the Calendars panel shows: every account with its calendars, the sources and their capability, and the
    credential bindings an account may name."""
    from core.kas.registry import DEFAULT_BASE_URLS

    accounts = [presented(account, now_fn=now_fn, get_connection_fn=get_connection_fn) for account in list_accounts(get_connection_fn=get_connection_fn)]
    bindings, bindings_error = [], ""
    try:
        bindings = list_credential_bindings()
    except Exception as exc:
        bindings_error = type(exc).__name__
    return {"ok": True, "accounts": accounts, "providers": PROVIDERS, "bindings": bindings, "bindings_error": bindings_error,
            "default_base_urls": dict(DEFAULT_BASE_URLS)}


__all__ = [
    "PROVIDERS",
    "RECOVERY",
    "add_account",
    "build_adapter",
    "classify_calendar_error",
    "decode_minutes",
    "disconnect_account",
    "discover_calendars",
    "list_accounts",
    "list_credential_bindings",
    "list_selections",
    "load_account",
    "present_account",
    "presented",
    "reconnect_account",
    "record_sync_result",
    "select_calendar",
    "set_opt_in",
    "settings_view",
]

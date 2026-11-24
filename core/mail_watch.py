"""New-mail alerts: one bell item and one chime for each batch of new mail, and the unread count on top.

While a mailbox that can be read is connected and Settings > Notifications > "Check new emails automatically"
is on (the default), a background poller checks each readable account every few minutes. It reads the inbox's
unread count and the headers (sender, subject, date, Message-ID) of the newest unread messages, through
``core.email_tools.unread_inbox_headers``: never a body or snippet, and never a flag change, so nothing is
marked read.

Rules this holds:

* Nothing runs without a mailbox. With no readable account the sweep reads the credential names and stops: no
  table is created, nothing is written and nothing goes online. A fresh install therefore never polls.
* The outbound switch rules like every other background check (``core.operator.calendar_accounts``): an account
  whose server is on this computer may always be checked; any other only while "Go online on its own" is on
  (``policy_engine.automatic_outbound_enabled``, which Local Only vetoes). When the switch blocks an account,
  its state says so and nothing is contacted.
* The first successful check of an account is its baseline: what is unread then is remembered without an alert,
  so connecting a mailbox with 300 unread messages does not ring.
* Deduplicated by Message-ID: every message alerted (or seen at baseline) is remembered in ``mail_watch_seen``,
  so a restart, a replayed sweep or two pollers never alert again. A message dated before the account's baseline
  (by more than a day) never alerts either, so an old unread message that becomes one of the newest later stays quiet.
* One batch of new messages -> one bell item (``new_mail``) -> at most one chime, through the notification
  centre's own audio claim (sound, quiet hours and the macOS owner apply).
* A failure is a state on the account, never a crash, with backoff; a refused sign-in waits longest, so a wrong
  saved password is not tried every few minutes.
"""
from __future__ import annotations

import contextlib
import email.utils
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

POLL_S = 120.0
TICK_S = 60.0
WINDOW = 20
SEEN_CAP = 5000
BACKOFF_BASE_S = 120.0
BACKOFF_CAP_S = 30 * 60.0
AUTH_BACKOFF_S = 30 * 60.0
SHOWN_IN_ITEM = 3
#: A message dated more than this before the account's baseline is old mail even if it was never seen. Generous on
#: purpose: a sender's clock or a slow relay can put a genuinely new message's Date hours in the past.
BASELINE_GRACE_S = 24 * 3600.0
BRIEF_SOURCE = "new_mail"

#: Account states the Settings line and the top icon read. Reason codes only; the page words them.
STATES = frozenset({"ok", "online_access_off", "needs_reconnect", "unreachable", "error"})
_AUTH_STATUSES = frozenset({"auth_failed", "needs_reauthorization", "needs_setup", "needs_credentials"})


def _utcnow() -> str:
    from core.time_authority import CLOCK

    return CLOCK.now_utc().isoformat()


def _default_connection():
    from storage.db import get_connection

    return get_connection()


def _epoch(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _message_epoch(value: Any) -> float | None:
    """A message's date as the provider gave it: an RFC 5322 Date header or an ISO time (Graph)."""
    text = str(value or "").strip()
    if not text:
        return None
    found = _epoch(text)
    if found is not None:
        return found
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _table_exists(conn: Any) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'mail_watch_accounts'").fetchone() is not None


def _ensure_tables(get_connection_fn: Callable[[], Any]) -> None:
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mail_watch_accounts (
                account TEXT PRIMARY KEY,
                mailbox TEXT NOT NULL DEFAULT '',
                baseline_at TEXT,
                unread INTEGER,
                state TEXT NOT NULL DEFAULT 'ok',
                last_checked_at TEXT,
                next_check_at TEXT,
                failures INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mail_watch_seen (
                account TEXT NOT NULL,
                message_key TEXT NOT NULL,
                provider_ref TEXT NOT NULL DEFAULT '',
                seen_at TEXT NOT NULL,
                PRIMARY KEY (account, message_key)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mail_watch_seen_ref ON mail_watch_seen(account, provider_ref)")
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- which mailboxes, may we go online


def readable_account_names() -> list[str]:
    """The account slots with a read transport, from the credential NAMES only (no secret is decrypted)."""
    from core import credential_store

    names = set()
    for row in credential_store.list_credentials():
        name = str(row.get("name") or "")
        if name.startswith("email.imap.") and len(name) > len("email.imap."):
            names.add(name[len("email.imap."):])
    return sorted(names)


def _readable_records() -> dict[str, Any]:
    from core.email_accounts import configured_accounts

    return {name: record for name, record in configured_accounts().items()
            if record.read and not record.error and record.coherent}


def _outbound_switch_on() -> bool:
    try:
        from core import policy_engine

        return policy_engine.automatic_outbound_enabled() is True
    except Exception:
        return False


def _reach(account: str) -> tuple[str, tuple[str, ...]]:
    """("ok", pinned IMAP addresses) when this account may be contacted now, else (state, ()).

    A server on this computer (an IMAP host that resolves only to loopback, a provider API base on loopback)
    may always be checked; anything else needs the outbound switch. Resolved IMAP addresses are pinned so the
    connection goes exactly where the check looked."""
    from core.email_tools import _load_account
    from core.provider_http import is_loopback_url

    creds = _load_account("imap", account) or {}
    provider = str(creds.get("provider") or "imap").lower()
    if provider in ("gmail", "graph"):
        if is_loopback_url(str(creds.get("api_base") or "")) and is_loopback_url(str(creds.get("token_url") or "")):
            return "ok", ()
        return ("ok", ()) if _outbound_switch_on() else ("online_access_off", ())
    from core.email_setup import _address_is_local, _resolve

    host, port = str(creds.get("host") or ""), int(creds.get("port") or 993)
    try:
        addresses = _resolve(host, port)
    except (OSError, UnicodeError, ValueError):
        addresses = ()
    if addresses and all(_address_is_local(address) for address in addresses):
        return "ok", addresses
    if not _outbound_switch_on():
        return "online_access_off", ()
    if not addresses:
        return "unreachable", ()
    return "ok", addresses


# --------------------------------------------------------------------------- one check of one account


def mailbox_fingerprint(account: str) -> str:
    """Which mailbox an account slot names now: provider, server and identity, hashed (no secret).

    The watcher's memory (baseline, seen messages, count) belongs to the MAILBOX, not to the slot name: a slot
    removed and set up again for another mailbox between two checks must start from a new baseline, or every
    unread message of the new mailbox would ring as new and the old count would show on top."""
    from core.email_tools import _load_account, configured_identity

    creds = _load_account("imap", account) or {}
    parts = [str(creds.get(name) or "").strip().lower() for name in ("provider", "host", "port", "api_base")]
    parts.append(configured_identity(account, creds))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def _fallback_key(entry: dict[str, Any]) -> str:
    """A message without a Message-ID is known by its provider ref, else by its own header text."""
    if entry.get("ref"):
        return "ref:" + str(entry["ref"])
    material = "\x1f".join(str(entry.get(name) or "") for name in ("from", "subject", "date"))
    return "hdr:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _display_sender(value: str) -> str:
    name, address = email.utils.parseaddr(str(value or ""))
    return (name or address or str(value or "")).strip()[:120]


class MailWatchPoller:
    """Checks every readable mailbox on a schedule. With no mailbox, or the switch off, it does nothing."""

    def __init__(self, *, tick_seconds: float = TICK_S, poll_seconds: float = POLL_S,
                 get_connection_fn: Callable[[], Any] = _default_connection,
                 now_fn: Callable[[], str] = _utcnow,
                 read_fn: Callable[..., Any] | None = None) -> None:
        self._tick = max(1.0, float(tick_seconds))
        self._poll = max(1.0, float(poll_seconds))
        self._get_connection_fn = get_connection_fn
        self._now_fn = now_fn
        self._read_fn = read_fn
        self._sweep_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the sweep, callable directly so tests use the same code ---------------------------------------

    def sweep_once(self) -> dict[str, Any]:
        """One pass over the accounts that are due. Never raises; a failure becomes the account's state."""
        outcome: dict[str, Any] = {"checked": 0, "alerts": 0, "blocked": 0, "errors": 0}
        if not self._sweep_lock.acquire(blocking=False):
            return outcome  # single flight: a sweep already running owns this pass
        try:
            return self._sweep(outcome)
        except Exception:
            outcome["errors"] += 1
            return outcome
        finally:
            self._sweep_lock.release()

    def _sweep(self, outcome: dict[str, Any]) -> dict[str, Any]:
        from core.operator import notification_center

        if not notification_center.load_preferences(get_connection_fn=self._get_connection_fn).get("check_new_mail"):
            return outcome  # switched off: no network, no write
        names = readable_account_names()
        conn = self._get_connection_fn()
        try:
            present = _table_exists(conn)
            rows = {row["account"]: dict(row) for row in conn.execute("SELECT * FROM mail_watch_accounts")} if present else {}
        finally:
            conn.close()
        if not names:
            if rows:
                self._forget_accounts(list(rows))  # every mailbox was removed: its count leaves the top bar
            return outcome  # no mailbox: nothing created, nothing online
        now_iso = self._now_fn()
        now_epoch = _epoch(now_iso) or time.time()
        # Due: its next check has come, or the slot now names another mailbox than the one its row remembers
        # (set up again under the same name), whose first check must not wait out the old mailbox's backoff.
        due = [name for name in names
               if (_epoch((rows.get(name) or {}).get("next_check_at")) or 0.0) <= now_epoch
               or (name in rows and str(rows[name].get("mailbox") or "") != mailbox_fingerprint(name))]
        gone = [name for name in rows if name not in names]
        if gone:
            self._forget_accounts(gone)
        if not due:
            return outcome
        records = _readable_records()
        _ensure_tables(self._get_connection_fn)
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("mail.watch.poller"):
            for name in due:
                if name not in records:
                    continue
                if not notification_center.load_preferences(get_connection_fn=self._get_connection_fn).get("check_new_mail"):
                    break  # switched off mid-sweep: no further mailbox is contacted
                try:
                    result = self._check(name, rows.get(name), now_iso=now_iso, now_epoch=now_epoch)
                except Exception:
                    outcome["errors"] += 1
                    self._save_state(name, rows.get(name), state="error", now_iso=now_iso, now_epoch=now_epoch,
                                     failed=True)
                    continue
                for key in ("checked", "alerts", "blocked", "errors"):
                    outcome[key] += result.get(key, 0)
        return outcome

    def _forget_accounts(self, names: list[str]) -> None:
        conn = self._get_connection_fn()
        try:
            marks = ",".join("?" for _ in names)
            conn.execute(f"DELETE FROM mail_watch_accounts WHERE account IN ({marks})", names)
            conn.execute(f"DELETE FROM mail_watch_seen WHERE account IN ({marks})", names)
            conn.commit()
        finally:
            conn.close()

    def _save_state(self, account: str, row: dict[str, Any] | None, *, state: str, now_iso: str, now_epoch: float,
                    failed: bool = False, unread: int | None = None, baseline_at: str | None = None,
                    mailbox: str = "") -> None:
        previous_failures = int((row or {}).get("failures") or 0)
        failures = previous_failures + 1 if failed else 0
        if not failed:
            wait = self._poll
        elif state == "needs_reconnect":
            wait = AUTH_BACKOFF_S
        elif state == "online_access_off":
            wait = self._poll
        else:
            wait = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** min(previous_failures, 8)))
        next_at = datetime.fromtimestamp(now_epoch + wait, tz=timezone.utc).isoformat()
        conn = self._get_connection_fn()
        try:
            conn.execute(
                """
                INSERT INTO mail_watch_accounts (account, mailbox, baseline_at, unread, state, last_checked_at,
                                                 next_check_at, failures)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account) DO UPDATE SET
                    mailbox = CASE WHEN excluded.mailbox != '' THEN excluded.mailbox ELSE mail_watch_accounts.mailbox END,
                    baseline_at = COALESCE(mail_watch_accounts.baseline_at, excluded.baseline_at),
                    unread = COALESCE(excluded.unread, mail_watch_accounts.unread),
                    state = excluded.state,
                    last_checked_at = CASE WHEN excluded.state = 'ok' THEN excluded.last_checked_at
                                           ELSE mail_watch_accounts.last_checked_at END,
                    next_check_at = excluded.next_check_at,
                    failures = excluded.failures
                """,
                (account, mailbox, baseline_at, unread, state, now_iso if state == "ok" else None, next_at, failures),
            )
            conn.commit()
        finally:
            conn.close()

    def _read(self, account: str, *, known_refs: frozenset[str], addresses: tuple[str, ...]):
        if self._read_fn is not None:
            return self._read_fn(account=account, window=WINDOW, known_refs=known_refs, addresses=addresses)
        from core.email_tools import unread_inbox_headers

        return unread_inbox_headers(account=account, window=WINDOW, known_refs=known_refs, addresses=addresses)

    def _check(self, account: str, row: dict[str, Any] | None, *, now_iso: str, now_epoch: float) -> dict[str, int]:
        mailbox = mailbox_fingerprint(account)
        if row is not None and str(row.get("mailbox") or "") != mailbox:
            self._forget_accounts([account])  # another mailbox under the same name: start from a new baseline
            row = None
        reach, addresses = _reach(account)
        if reach != "ok":
            self._save_state(account, row, state=reach, now_iso=now_iso, now_epoch=now_epoch, failed=True,
                             mailbox=mailbox)
            return {"blocked": 1}
        conn = self._get_connection_fn()
        try:
            seen_rows = conn.execute("SELECT message_key, provider_ref FROM mail_watch_seen WHERE account = ?",
                                     (account,)).fetchall()
        finally:
            conn.close()
        seen_keys = {r["message_key"] for r in seen_rows}
        known_refs = frozenset(r["provider_ref"] for r in seen_rows if r["provider_ref"])
        result = self._read(account, known_refs=known_refs, addresses=addresses)
        if not getattr(result, "ok", False):
            status = str(getattr(result, "status", "") or "")
            state = "needs_reconnect" if status in _AUTH_STATUSES else "unreachable"
            self._save_state(account, row, state=state, now_iso=now_iso, now_epoch=now_epoch, failed=True,
                             mailbox=mailbox)
            return {"errors": 1}
        baseline_at = (row or {}).get("baseline_at")
        baseline_epoch = _epoch(baseline_at)
        fresh: list[dict[str, Any]] = []
        remembered: list[tuple[str, str]] = []
        for entry in result.messages:
            if entry.get("known"):
                continue
            key = str(entry.get("key") or "") or _fallback_key(entry)
            if key in seen_keys:
                if entry.get("ref"):
                    remembered.append((key, str(entry["ref"])))  # learn the ref so the next check skips it
                continue
            seen_keys.add(key)
            remembered.append((key, str(entry.get("ref") or "")))
            if baseline_epoch is None:
                continue  # the baseline: remember what is unread now, alert nothing
            dated = _message_epoch(entry.get("date"))
            if dated is not None and dated < baseline_epoch - BASELINE_GRACE_S:
                continue  # an old unread message surfacing later is not new mail
            fresh.append(entry)
        alerts = 0
        conn = self._get_connection_fn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for key, ref in remembered:
                conn.execute(
                    """
                    INSERT INTO mail_watch_seen (account, message_key, provider_ref, seen_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(account, message_key) DO UPDATE SET
                        provider_ref = CASE WHEN excluded.provider_ref != '' THEN excluded.provider_ref
                                            ELSE mail_watch_seen.provider_ref END
                    """,
                    (account, key, ref, now_iso),
                )
            conn.execute(
                """
                DELETE FROM mail_watch_seen WHERE account = ? AND rowid NOT IN (
                    SELECT rowid FROM mail_watch_seen WHERE account = ? ORDER BY seen_at DESC LIMIT ?)
                """,
                (account, account, SEEN_CAP),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if fresh:
            if self._notify(account, fresh):
                alerts = 1
        self._save_state(account, row, state="ok", now_iso=now_iso, now_epoch=now_epoch, unread=int(result.unread),
                         baseline_at=baseline_at or now_iso, mailbox=mailbox)
        return {"checked": 1, "alerts": alerts}

    def _notify(self, account: str, fresh: list[dict[str, Any]]) -> bool:
        """One bell item for this batch. Its key is the batch's Message-IDs, so a replay adds nothing."""
        from core.operator import notification_center
        from core.operator.notification_audio import enabled_now

        fresh = sorted(fresh, key=lambda entry: _message_epoch(entry.get("date")) or 0.0, reverse=True)
        keys = sorted(str(entry.get("key") or _fallback_key(entry)) for entry in fresh)
        digest = hashlib.sha256(json.dumps([account, keys]).encode("utf-8")).hexdigest()[:24]
        preferences = notification_center.load_preferences(get_connection_fn=self._get_connection_fn)
        details = bool(preferences.get("mail_alert_details", True))
        count = len(fresh)
        # Details off: the alert holds the count only. No sender or subject is stored in it, so neither the bell,
        # macOS nor a later setting change can show them.
        shown = [{"from": _display_sender(entry.get("from", "")), "subject": str(entry.get("subject") or "")[:200]}
                 for entry in fresh[:SHOWN_IN_ITEM]] if details else []
        if count == 1:
            title = f"New email from {shown[0]['from'] or 'an unknown sender'}" if shown else "1 new email"
        else:
            title = f"{count} new emails"
        body = "; ".join(
            (f"{row['from']}: {row['subject']}" if row["subject"] else row["from"]) for row in shown
        ) + (f" and {count - len(shown)} more" if shown and count > len(shown) else "")
        item = notification_center.record_item(
            dedupe_key=f"new_mail:{account}:{digest}", source_kind="new_mail", title=title, body=body,
            payload={"kind": "new_mail", "section": "updates", "account": account, "count": count,
                     "messages": shown, "audio_eligible": enabled_now(preferences=preferences),
                     "audio_owner": "native" if preferences.get("native_notifications") else "browser"},
            now_fn=self._now_fn, get_connection_fn=self._get_connection_fn,
        )
        if item.get("created"):
            notification_center.record_delivery(item["notification_id"], channel="in_app", state="recorded",
                                                now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)
        return bool(item.get("created"))

    # -- thread lifecycle ------------------------------------------------------------------------------

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vool-mail-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                self.sweep_once()
            self._stop.wait(self._tick)


_DEFAULT_POLLER: MailWatchPoller | None = None
_POLLER_LOCK = threading.Lock()


DISABLE_ENV = "VOOL_MAIL_WATCH"


def start_default_poller() -> MailWatchPoller | None:
    """Start the one process-wide mail watcher. Called by the served boot; safe to call twice.

    ``VOOL_MAIL_WATCH=0`` keeps it off for an embedded run or a test rig that pins every request a chat turn makes
    to a mail provider (the watcher's own reads would interleave with them)."""
    global _DEFAULT_POLLER

    if str(os.environ.get(DISABLE_ENV) or "").strip().lower() in {"0", "off", "false"}:
        return None
    with _POLLER_LOCK:
        if _DEFAULT_POLLER is not None and _DEFAULT_POLLER._thread is not None and _DEFAULT_POLLER._thread.is_alive():
            return _DEFAULT_POLLER
        _DEFAULT_POLLER = MailWatchPoller()
        _DEFAULT_POLLER.start()
        return _DEFAULT_POLLER


def stop_default_poller() -> None:
    global _DEFAULT_POLLER

    with _POLLER_LOCK:
        if _DEFAULT_POLLER is not None:
            _DEFAULT_POLLER.stop()
        _DEFAULT_POLLER = None


# --------------------------------------------------------------------------- what the page reads


def unread_now(account: str = "") -> dict[str, Any]:
    """The inbox unread count read FRESH from the mailbox now, for an answer that says "you have N unread".

    Owner rule (2026-11-24): a mail count is always read from the mailbox, never from memory or a stored copy. This
    asks the server through the same header-only read the watcher uses (no body, nothing marked read) and returns
    per-account results; a mailbox that cannot be read now is reported as such with ``unread`` None, never as zero
    and never as an older number. A successful read also refreshes the top mail icon's count."""
    from core.email_accounts import configured_accounts
    from core.email_tools import unread_inbox_headers

    names = [name for name, record in configured_accounts().items() if record.read and not record.error]
    if account:
        names = [name for name in names if name == account]
    rows: list[dict[str, Any]] = []
    for name in names:
        result = unread_inbox_headers(account=name, window=1)
        rows.append({"account": name, "ok": bool(result.ok), "status": result.status,
                     "unread": int(result.unread) if result.ok else None})
        if result.ok:
            with contextlib.suppress(Exception):
                conn = _default_connection()
                try:
                    if _table_exists(conn):
                        conn.execute("UPDATE mail_watch_accounts SET unread = ? WHERE account = ?",
                                     (int(result.unread), name))
                        conn.commit()
                finally:
                    conn.close()
    total = sum(row["unread"] for row in rows) if rows and all(row["ok"] for row in rows) else None
    return {"unread": total, "read_at": _utcnow(), "accounts": rows}


def status_view(*, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """The top mail icon and the Settings line: stored state only, no network and no secret read.

    ``connected`` is whether a readable mailbox exists; ``unread`` is the sum of the last known counts (None
    before the first check); ``state`` is the worst account state, so a blocked check is never shown as zero."""
    from core.operator import notification_center

    enabled = bool(notification_center.load_preferences(get_connection_fn=get_connection_fn).get("check_new_mail"))
    names = readable_account_names()
    conn = get_connection_fn()
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM mail_watch_accounts ORDER BY account")] \
            if _table_exists(conn) else []
    finally:
        conn.close()
    rows = [row for row in rows if row["account"] in names]
    known = [int(row["unread"]) for row in rows if row.get("unread") is not None]
    order = ["online_access_off", "needs_reconnect", "unreachable", "error"]
    states = [str(row.get("state") or "ok") for row in rows]
    worst = next((state for state in order if state in states), "ok" if rows else "")
    return {
        "ok": True,
        "enabled": enabled,
        "connected": bool(names),
        "unread": sum(known) if known else None,
        "state": worst if enabled else "",
        "last_checked": max((str(row.get("last_checked_at") or "") for row in rows), default=""),
        "accounts": [{"account": row["account"], "unread": row.get("unread"), "state": row.get("state") or "ok",
                      "last_checked": row.get("last_checked_at") or ""} for row in rows],
    }


__all__ = [
    "MailWatchPoller",
    "readable_account_names",
    "start_default_poller",
    "status_view",
    "stop_default_poller",
    "unread_now",
]

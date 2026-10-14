"""Add or remove a password (IMAP/SMTP) email account from Settings.

An account is stored as the same credential-store entries the account authority reads
(core/email_accounts.py): `email.imap.<slot>` and, when an outgoing server is given, `email.smtp.<slot>`,
each one JSON object in the shape core/email_tools.py documents. The slot name is the address made
readable plus a short hash of the full address, so two different addresses never share a slot.

Adding is check-then-store, and fails closed:

1. every field is validated (no control characters, bounded lengths, ports in range, a security mode
   of TLS or STARTTLS only; the port only suggests the default);
2. each server name is resolved ONCE. When Local Only is in force, every address it resolves to must be
   on this machine or its network, or the request is refused before any connection. The login then
   connects to exactly those addresses (TLS still verifies the server name), so a second lookup cannot
   send it somewhere else;
3. an existing slot is never overwritten unless the caller asked to replace it, a slot holding another
   mailbox is never overwritten at all, and an account that another setup owns (an OAuth grant) is never
   replaced from here;
4. the password is checked by logging in to the IMAP server and, when one is given, the SMTP server,
   through the connectors the mail tools use (`email_tools._imap_connect` / `_smtp_connect`: verifying TLS
   context, bounded timeout);
5. only after every login succeeded is anything written; a failed write puts back what was there,
   labels included.

A failure is reported as a plain kind (`auth_failed`, `smtp_auth_failed`, `cannot_reach`, `tls_failed`,
`timeout`, ...) with a fixed sentence. Server replies and exception text are never echoed, because a
server may quote what it was sent. No password is logged, and no response carries one.

Removing deletes the slot's entries and clears any default-account choice that named the slot.
"""
from __future__ import annotations

import contextlib
import hashlib
import imaplib
import ipaddress
import json
import re
import smtplib
import socket
import ssl
import threading
from typing import Any

IMAP_DEFAULT_PORT = 993
SMTP_DEFAULT_PORT = 465
#: How long one login check may wait on a server. Tests shorten it; nothing a running VOOL reads sets it.
VERIFY_TIMEOUT_S = 30.0
SECURITY_MODES = ("ssl", "starttls")
#: Ports whose usual security is the STARTTLS upgrade; any other port defaults to implicit TLS.
#: Only a default: the caller may choose either mode explicitly. Cleartext is never offered.
_STARTTLS_PORTS = {"imap": frozenset({143}), "smtp": frozenset({25, 587})}
_ADD_FIELDS = frozenset({
    "address", "username", "password", "imap_host", "imap_port", "imap_security",
    "smtp_host", "smtp_port", "smtp_security", "replace",
})
_REMOVE_FIELDS = frozenset({"account"})
_ADDRESS_RE = re.compile(r"[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_MAX_PASSWORD = 1024
_MAX_USERNAME = 320
_OAUTH_PROVIDERS = ("gmail", "graph")
_DEFAULT_CATEGORY = "default_account.email"

#: One add or remove at a time, so the existence check and the write cannot interleave.
_lock = threading.Lock()

MESSAGES = {
    "invalid": "Some details are missing or not valid. Nothing was saved.",
    "password_charset": "This password has characters a mail server sign-in cannot carry. Nothing was saved.",
    "local_only": "Local Only is on, so VOOL did not contact this mail server. Nothing was saved.",
    "exists": "This email account is already set up. Choose replace to save the new details.",
    "conflict": "Another mailbox is already saved under this account name, so nothing was changed.",
    "managed_elsewhere": "This account was connected through its provider's sign-in, so it is changed there, not here.",
    "auth_failed": "The incoming mail server did not accept this username and password. Nothing was saved.",
    "cannot_reach": "VOOL could not reach the incoming mail server. Check the server name and port. Nothing was saved.",
    "tls_failed": "The incoming mail server's secure connection could not be verified, so VOOL did not send the password. Nothing was saved.",
    "timeout": "The incoming mail server did not answer in time. Nothing was saved.",
    "smtp_auth_failed": "The outgoing mail server did not accept this username and password. Nothing was saved.",
    "smtp_cannot_reach": "VOOL could not reach the outgoing mail server. Check the server name and port. Nothing was saved.",
    "smtp_tls_failed": "The outgoing mail server's secure connection could not be verified, so VOOL did not send the password. Nothing was saved.",
    "smtp_timeout": "The outgoing mail server did not answer in time. Nothing was saved.",
    "store_failed": "VOOL could not save the change securely on this machine. Check local storage and try again.",
    "not_found": "There is no such email account.",
    "failed": "Something went wrong. Nothing was saved.",
}


class EmailSetupError(Exception):
    """A refused or failed step: a kind the Settings page names, and the field it is about (if any)."""

    def __init__(self, kind: str, *, field: str = "", status: int = 400):
        super().__init__(kind)
        self.kind, self.field, self.status = kind, field, status

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "error": self.kind, "message": MESSAGES.get(self.kind, MESSAGES["failed"])}
        if self.field:
            out["field"] = self.field
        return out


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------


def _has_control(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _text(body: dict[str, Any], name: str, *, required: bool, limit: int) -> str:
    value = body.get(name)
    if value is None or value == "":
        if required:
            raise EmailSetupError("invalid", field=name)
        return ""
    if not isinstance(value, str):
        raise EmailSetupError("invalid", field=name)
    value = value.strip()
    if (required and not value) or len(value) > limit or _has_control(value):
        raise EmailSetupError("invalid", field=name)
    return value


def _host(body: dict[str, Any], name: str, *, required: bool) -> str:
    value = _text(body, name, required=required, limit=253).lower()
    if not value:
        return ""
    with contextlib.suppress(ValueError):
        return str(ipaddress.ip_address(value))
    if not _HOST_RE.fullmatch(value) or ".." in value:
        raise EmailSetupError("invalid", field=name)
    return value


def _port(body: dict[str, Any], name: str, default: int) -> int:
    value = body.get(name)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise EmailSetupError("invalid", field=name)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 1 <= value <= 65535:
        raise EmailSetupError("invalid", field=name)
    return value


def default_security(kind: str, port: int) -> str:
    """The usual security for a port: STARTTLS on 143 (IMAP) and 25/587 (SMTP), implicit TLS otherwise."""
    return "starttls" if port in _STARTTLS_PORTS[kind] else "ssl"


def _security(body: dict[str, Any], kind: str, port: int) -> str:
    name = f"{kind}_security"
    value = body.get(name)
    if value is None or value == "":
        return default_security(kind, port)
    if not isinstance(value, str) or value.strip().lower() not in SECURITY_MODES:
        raise EmailSetupError("invalid", field=name)
    return value.strip().lower()


def slot_for_address(address: str) -> str:
    """The account slot one address lands in: a readable part plus 10 hex digits of the address's hash.

    Lowercase letters, digits and '-' only (an OAuth binding name splits on dots). The hash makes the
    slot unique per address even where the readable parts agree (a.b@x and a_b@x) or are cut short."""
    clean = str(address or "").strip().lower()
    if not clean:
        raise EmailSetupError("invalid", field="address")
    readable = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")[:40].strip("-")
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}" if readable else digest


def _parse_add(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise EmailSetupError("invalid")
    extra = set(body) - _ADD_FIELDS
    if extra:
        raise EmailSetupError("invalid", field=sorted(extra)[0])
    address = _text(body, "address", required=True, limit=254).lower()
    if not _ADDRESS_RE.fullmatch(address):
        raise EmailSetupError("invalid", field="address")
    password = body.get("password")
    # Leading and trailing spaces are part of a password, so it is not trimmed.
    if not isinstance(password, str) or not password or len(password) > _MAX_PASSWORD or _has_control(password):
        raise EmailSetupError("invalid", field="password")
    if not password.isascii():
        # The IMAP LOGIN command carries ASCII only; refused up front rather than half-sent.
        raise EmailSetupError("password_charset", field="password")
    username = _text(body, "username", required=False, limit=_MAX_USERNAME) or address
    if not username.isascii():
        raise EmailSetupError("invalid", field="username")
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        raise EmailSetupError("invalid", field="replace")
    imap_host = _host(body, "imap_host", required=True)
    imap_port = _port(body, "imap_port", IMAP_DEFAULT_PORT)
    imap_security = _security(body, "imap", imap_port)
    smtp_host = _host(body, "smtp_host", required=False)
    if not smtp_host and (body.get("smtp_port") not in (None, "") or body.get("smtp_security") not in (None, "")):
        raise EmailSetupError("invalid", field="smtp_host")
    smtp_port = _port(body, "smtp_port", SMTP_DEFAULT_PORT)
    smtp_security = _security(body, "smtp", smtp_port) if smtp_host else ""
    return {
        "address": address, "username": username, "password": password, "replace": replace,
        "imap_host": imap_host, "imap_port": imap_port, "imap_security": imap_security,
        "smtp_host": smtp_host, "smtp_port": smtp_port, "smtp_security": smtp_security,
    }


# ---------------------------------------------------------------------------------------------
# Network policy: resolve once, check, connect to what was checked
# ---------------------------------------------------------------------------------------------


def _local_only_in_force() -> bool:
    """The same two authorities every other outbound door asks: ``remote_fetch_policy.local_only_active``
    (the turn's lane or the machine profile) and ``policy_engine.local_only_mode``. A policy that cannot
    be read counts as Local Only."""
    try:
        from core.policy_engine import local_only_mode
        from core.remote_fetch_policy import local_only_active

        return bool(local_only_active(None)) or bool(local_only_mode())
    except Exception:
        return True


def _address_is_local(address: str) -> bool:
    """``auto_local_only_mode.endpoint_is_local`` on an IP literal (no lookup); unknown is not local."""
    try:
        from core.auto_local_only_mode import endpoint_is_local

        literal = f"[{address}]" if ":" in address else address
        return bool(endpoint_is_local(f"imap://{literal}"))
    except Exception:
        return False


def _resolve(host: str, port: int) -> tuple[str, ...]:
    """Every address ``host`` resolves to, in the resolver's order, without duplicates."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return tuple(seen)


def _checked_addresses(host: str, port: int, *, local_only: bool, unreachable: str) -> tuple[str, ...]:
    try:
        addresses = _resolve(host, port)
    except (OSError, UnicodeError, ValueError):
        addresses = ()
    if local_only and (not addresses or not all(_address_is_local(address) for address in addresses)):
        raise EmailSetupError("local_only", status=403)
    if not addresses:
        raise EmailSetupError(unreachable)
    return addresses


# ---------------------------------------------------------------------------------------------
# The login checks
# ---------------------------------------------------------------------------------------------


def verify_imap_login(host: str, port: int, security: str, addresses: tuple[str, ...], username: str, password: str) -> None:
    """Log in once and log out. Raises EmailSetupError with a kind; never returns server text.

    The connection is opened by ``email_tools._imap_connect`` (the connector every mail read uses) on
    the already-checked ``addresses``: ``ssl.create_default_context()`` verifies the certificate against
    the server NAME, and a certificate that does not verify ends the attempt before the password is sent."""
    from core.email_tools import _imap_connect

    try:
        client = _imap_connect({"host": host, "port": port, "security": security}, addresses=addresses,
                               timeout=VERIFY_TIMEOUT_S)
    except TimeoutError:
        raise EmailSetupError("timeout") from None
    except ssl.SSLError:
        raise EmailSetupError("tls_failed") from None
    except imaplib.IMAP4.error:
        # A STARTTLS the server does not offer is a security failure; anything else is a greeting
        # that was not an IMAP server's.
        raise EmailSetupError("tls_failed" if security == "starttls" else "cannot_reach") from None
    except (OSError, ValueError):
        raise EmailSetupError("cannot_reach") from None
    try:
        client.login(username, password)
    except imaplib.IMAP4.abort:
        raise EmailSetupError("cannot_reach") from None
    except imaplib.IMAP4.error:
        raise EmailSetupError("auth_failed") from None
    except TimeoutError:
        raise EmailSetupError("timeout") from None
    except ssl.SSLError:
        raise EmailSetupError("tls_failed") from None
    except OSError:
        raise EmailSetupError("cannot_reach") from None
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def verify_smtp_login(host: str, port: int, security: str, addresses: tuple[str, ...], username: str, password: str) -> None:
    """Log in to the outgoing server once and quit, through ``email_tools._smtp_connect`` (the connector
    every send uses) on the already-checked ``addresses``. Nothing is sent. Raises EmailSetupError."""
    from core.email_tools import _smtp_connect

    try:
        client = _smtp_connect({"host": host, "port": port, "security": security}, addresses=addresses,
                               timeout=VERIFY_TIMEOUT_S)
    except TimeoutError:
        raise EmailSetupError("smtp_timeout") from None
    except ssl.SSLError:
        raise EmailSetupError("smtp_tls_failed") from None
    except smtplib.SMTPNotSupportedError:
        raise EmailSetupError("smtp_tls_failed") from None  # STARTTLS not offered
    except (OSError, ValueError):
        raise EmailSetupError("smtp_cannot_reach") from None
    try:
        client.login(username, password)
    except smtplib.SMTPServerDisconnected:
        raise EmailSetupError("smtp_cannot_reach") from None
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPNotSupportedError):
        raise EmailSetupError("smtp_auth_failed") from None
    except TimeoutError:
        raise EmailSetupError("smtp_timeout") from None
    except ssl.SSLError:
        raise EmailSetupError("smtp_tls_failed") from None
    except smtplib.SMTPException:
        raise EmailSetupError("smtp_auth_failed") from None  # e.g. no authentication method in common
    except OSError:
        raise EmailSetupError("smtp_cannot_reach") from None
    finally:
        with contextlib.suppress(Exception):
            client.quit()
        with contextlib.suppress(Exception):
            client.close()


# ---------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------


def _names(slot: str) -> tuple[str, str]:
    return f"email.imap.{slot}", f"email.smtp.{slot}"


def _oauth_owned(slot: str) -> bool:
    from core.credential_store import has_credential

    return any(has_credential(f"email.oauth.{provider}.{slot}") for provider in _OAUTH_PROVIDERS)


def _stored_entries(slot: str) -> list[dict[str, Any]]:
    """The slot's stored transport blobs (read inside strict reads; an unreadable one is marked)."""
    from core.credential_store import get_credential, strict_reads

    blobs: list[dict[str, Any]] = []
    with strict_reads():
        for name in _names(slot):
            raw = get_credential(name)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            blobs.append(data if isinstance(data, dict) else {"_unreadable": True})
    return blobs


def _stored_address(blob: dict[str, Any]) -> str:
    return str(blob.get("from_addr") or blob.get("username") or "").strip().lower()


def _write_entries(entries: dict[str, str | None], labels: dict[str, str]) -> None:
    """Apply {name: value or None (delete)}; on any failure put every touched name back as it was,
    with the label it had."""
    from core.credential_store import (
        delete_credential,
        get_credential,
        list_credentials,
        store_credential,
        strict_reads,
    )

    try:
        with strict_reads():
            previous = {name: get_credential(name) for name in entries}
        previous_labels = {str(row.get("name") or ""): str(row.get("label") or "") for row in list_credentials()}
    except Exception:
        raise EmailSetupError("store_failed", status=500) from None
    touched: list[str] = []
    try:
        for name, value in entries.items():
            touched.append(name)
            if value is None:
                delete_credential(name)
            else:
                store_credential(name, value, label=labels.get(name, ""))
    except Exception:
        for name in reversed(touched):
            with contextlib.suppress(Exception):
                if previous.get(name) is None:
                    delete_credential(name)
                else:
                    store_credential(name, str(previous[name]), label=previous_labels.get(name, ""))
        raise EmailSetupError("store_failed", status=500) from None


def _clear_defaults(slot: str) -> int:
    """Forget every default-account choice (any principal, any scope) that names ``slot``."""
    from core import operator_profile
    from core.email_accounts import slot_of_binding

    conn = operator_profile._conn()
    try:
        rows = conn.execute("SELECT * FROM operator_profile_items WHERE category = ?", (_DEFAULT_CATEGORY,)).fetchall()
    finally:
        conn.close()
    cleared = 0
    for row in rows:
        item = operator_profile._row_to_item(row)
        if item.status == operator_profile.STATUS_DELETED or not isinstance(item.value, dict):
            continue
        if slot_of_binding(str(item.value.get("binding_ref") or "")) != slot:
            continue
        change = operator_profile.forget_item(item.item_id, actor="operator", reason="email account removed")
        if change.kind != "forgotten":
            raise RuntimeError("default not cleared")
        cleared += 1
    return cleared


def add_account(body: Any) -> dict[str, Any]:
    from core.credential_store import has_credential

    spec = _parse_add(body)
    slot = slot_for_address(spec["address"])
    local_only = _local_only_in_force()
    imap_addresses = _checked_addresses(spec["imap_host"], spec["imap_port"], local_only=local_only,
                                        unreachable="cannot_reach")
    smtp_addresses: tuple[str, ...] = ()
    if spec["smtp_host"]:
        smtp_addresses = _checked_addresses(spec["smtp_host"], spec["smtp_port"], local_only=local_only,
                                            unreachable="smtp_cannot_reach")
    imap_name, smtp_name = _names(slot)

    def _check_slot() -> None:
        if _oauth_owned(slot):
            raise EmailSetupError("managed_elsewhere", status=409)
        if not (has_credential(imap_name) or has_credential(smtp_name)):
            return
        try:
            blobs = _stored_entries(slot)
        except Exception:
            raise EmailSetupError("store_failed", status=500) from None
        if any(str(blob.get("provider") or "imap").strip().lower() in _OAUTH_PROVIDERS for blob in blobs):
            raise EmailSetupError("managed_elsewhere", status=409)
        # A slot that holds another mailbox (or one that cannot be read) is never overwritten.
        if any(blob.get("_unreadable") or _stored_address(blob) != spec["address"] for blob in blobs):
            raise EmailSetupError("conflict", status=409)
        if not spec["replace"]:
            raise EmailSetupError("exists", status=409)

    _check_slot()  # refuse early: no login is attempted for a slot that would be refused anyway
    verify_imap_login(spec["imap_host"], spec["imap_port"], spec["imap_security"], imap_addresses,
                      spec["username"], spec["password"])
    if spec["smtp_host"]:
        verify_smtp_login(spec["smtp_host"], spec["smtp_port"], spec["smtp_security"], smtp_addresses,
                          spec["username"], spec["password"])

    imap_blob = {"host": spec["imap_host"], "port": spec["imap_port"], "security": spec["imap_security"],
                 "username": spec["username"], "password": spec["password"], "from_addr": spec["address"]}
    entries: dict[str, str | None] = {imap_name: json.dumps(imap_blob, sort_keys=True)}
    labels = {imap_name: f"Email reading · {spec['address']}", smtp_name: f"Email sending · {spec['address']}"}
    if spec["smtp_host"]:
        smtp_blob = {"host": spec["smtp_host"], "port": spec["smtp_port"], "security": spec["smtp_security"],
                     "username": spec["username"], "password": spec["password"], "from_addr": spec["address"]}
        entries[smtp_name] = json.dumps(smtp_blob, sort_keys=True)
    with _lock:
        _check_slot()  # the slot may have been taken while the logins were in flight
        if not spec["smtp_host"] and has_credential(smtp_name):
            entries[smtp_name] = None  # a replaced account must not keep the old sending entry
        _write_entries(entries, labels)
    if spec["smtp_host"]:
        message = "The incoming and outgoing mail servers accepted the sign-in, and the account is saved."
    else:
        message = "The incoming mail server accepted the sign-in, and the account is saved. It can read mail but not send it."
    return {"ok": True, "account": slot, "address": spec["address"], "read": True, "send": bool(spec["smtp_host"]),
            "message": message}


def remove_account(body: Any) -> dict[str, Any]:
    from core.credential_store import has_credential

    if not isinstance(body, dict) or set(body) - _REMOVE_FIELDS:
        raise EmailSetupError("invalid")
    account = body.get("account")
    if not isinstance(account, str) or not account.strip() or len(account) > 128 or _has_control(account):
        raise EmailSetupError("invalid", field="account")
    account = account.strip()
    with _lock:
        names = [name for name in _names(account) if has_credential(name)]
        if not names:
            raise EmailSetupError("not_found", status=404)
        if _oauth_owned(account):
            raise EmailSetupError("managed_elsewhere", status=409)
        try:
            blobs = _stored_entries(account)
        except Exception:
            raise EmailSetupError("store_failed", status=500) from None
        if any(str(blob.get("provider") or "imap").strip().lower() in _OAUTH_PROVIDERS for blob in blobs):
            raise EmailSetupError("managed_elsewhere", status=409)
        address = next((_stored_address(blob) for blob in blobs if _stored_address(blob)), "")
        _write_entries({name: None for name in names}, {})
        if any(has_credential(name) for name in names):
            raise EmailSetupError("store_failed", status=500)
        try:
            cleared, default_ok = _clear_defaults(account), True
        except Exception:
            cleared, default_ok = 0, False
    out: dict[str, Any] = {"ok": True, "account": account, "address": address, "default_cleared": cleared > 0,
                           "message": "The account is removed from this machine."}
    if not default_ok:
        out["default_error"] = "store_failed"
        out["message"] = "The account is removed from this machine, but its default choice could not be cleared."
    return out


def handle(action: str, body: Any) -> tuple[int, dict[str, Any]]:
    """One Settings action ('add' or 'remove') → (HTTP status, payload). No payload carries a password."""
    run = {"add": add_account, "remove": remove_account}.get(action)
    if run is None:
        return 400, EmailSetupError("invalid").payload()
    try:
        return 200, run(body)
    except EmailSetupError as exc:
        return exc.status, exc.payload()
    except Exception:
        # Unknown outcome: report it without the exception text, which may quote a server or a value.
        return 500, EmailSetupError("failed").payload()


__all__ = [
    "MESSAGES",
    "SECURITY_MODES",
    "EmailSetupError",
    "add_account",
    "default_security",
    "handle",
    "remove_account",
    "slot_for_address",
    "verify_imap_login",
    "verify_smtp_login",
]

"""Add or remove a password (IMAP/SMTP) email account from Settings.

An account is stored as the same credential-store entries the account authority reads
(core/email_accounts.py): `email.imap.<slot>` and, when a sending server is given, `email.smtp.<slot>`,
each one JSON object in the shape core/email_tools.py documents. The slot name is derived from the
address, so one mailbox always lands in one slot.

Adding is check-then-store, and fails closed:

1. every field is validated (no control characters, bounded lengths, ports in range);
2. Local Only is respected: when it is in force and a server is not on this machine or its network,
   the request is refused before any connection is opened;
3. an existing slot is never overwritten unless the caller asked to replace it, and an account that
   another setup owns (an OAuth grant) is never replaced from here;
4. the password is checked by logging in to the IMAP server through the same connector the mail
   reader uses (`email_tools._imap_connect`: a verifying TLS context and a bounded timeout);
5. only after a successful login is anything written; a failed write restores what was there.

A failure is reported as a plain kind (`auth_failed`, `cannot_reach`, `tls_failed`, `timeout`, ...)
with a fixed sentence. Server replies and exception text are never echoed, because a server may quote
what it was sent. No password is logged, and no response carries one.
"""
from __future__ import annotations

import contextlib
import imaplib
import ipaddress
import json
import re
import ssl
import threading
from typing import Any

IMAP_DEFAULT_PORT = 993
SMTP_DEFAULT_PORT = 465
#: Ports that use the STARTTLS upgrade; every other port is implicit TLS. Cleartext is never offered.
_STARTTLS_PORTS = {"imap": frozenset({143}), "smtp": frozenset({25, 587})}
_ADD_FIELDS = frozenset({"address", "username", "password", "imap_host", "imap_port", "smtp_host", "smtp_port", "replace"})
_REMOVE_FIELDS = frozenset({"account"})
_ADDRESS_RE = re.compile(r"[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_MAX_PASSWORD = 1024
_MAX_USERNAME = 320
_OAUTH_PROVIDERS = ("gmail", "graph")

#: One add or remove at a time, so the existence check and the write cannot interleave.
_lock = threading.Lock()

MESSAGES = {
    "invalid": "Some details are missing or not valid. Nothing was saved.",
    "local_only": "Local Only is on, so VOOL did not contact this mail server. Nothing was saved.",
    "exists": "This email account is already set up. Choose replace to save the new details.",
    "managed_elsewhere": "This account was connected through its provider's sign-in, so it is changed there, not here.",
    "auth_failed": "The mail server did not accept this username and password. Nothing was saved.",
    "cannot_reach": "VOOL could not reach this mail server. Check the server name and port. Nothing was saved.",
    "tls_failed": "The mail server's security certificate could not be verified, so VOOL did not send the password. Nothing was saved.",
    "timeout": "The mail server did not answer in time. Nothing was saved.",
    "store_failed": "The login worked, but VOOL could not save the account securely on this machine. Check local storage and try again.",
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


def _security(kind: str, port: int) -> str:
    return "starttls" if port in _STARTTLS_PORTS[kind] else "ssl"


def slot_for_address(address: str) -> str:
    """The account slot one address lands in: lowercase letters and digits joined by '-'.

    No dots (an OAuth binding name splits on them), no path or wildcard characters, bounded length."""
    slot = re.sub(r"[^a-z0-9]+", "-", str(address or "").strip().lower()).strip("-")[:64].strip("-")
    if not slot:
        raise EmailSetupError("invalid", field="address")
    return slot


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
    # The IMAP LOGIN command carries ASCII only; a password it cannot carry is refused up front
    # rather than half-sent. Leading and trailing spaces are part of a password, so it is not trimmed.
    if (not isinstance(password, str) or not password or len(password) > _MAX_PASSWORD
            or _has_control(password) or not password.isascii()):
        raise EmailSetupError("invalid", field="password")
    username = _text(body, "username", required=False, limit=_MAX_USERNAME) or address
    if not username.isascii():
        raise EmailSetupError("invalid", field="username")
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        raise EmailSetupError("invalid", field="replace")
    imap_host = _host(body, "imap_host", required=True)
    imap_port = _port(body, "imap_port", IMAP_DEFAULT_PORT)
    smtp_host = _host(body, "smtp_host", required=False)
    if not smtp_host and body.get("smtp_port") not in (None, ""):
        raise EmailSetupError("invalid", field="smtp_host")
    smtp_port = _port(body, "smtp_port", SMTP_DEFAULT_PORT)
    return {
        "address": address, "username": username, "password": password, "replace": replace,
        "imap_host": imap_host, "imap_port": imap_port, "smtp_host": smtp_host, "smtp_port": smtp_port,
    }


# ---------------------------------------------------------------------------------------------
# Network policy and the login check
# ---------------------------------------------------------------------------------------------


def _refused_by_local_only(hosts: list[str]) -> bool:
    """True when Local Only binds and any of these servers is not on this machine or its network.

    The same two authorities every other outbound door asks: ``remote_fetch_policy.local_only_active``
    (the turn's lane or the machine profile) and ``policy_engine.local_only_mode``; "local" is decided
    by ``auto_local_only_mode.endpoint_is_local``. A policy that cannot be read counts as Local Only,
    and a server that cannot be classified counts as not local."""
    try:
        from core.policy_engine import local_only_mode
        from core.remote_fetch_policy import local_only_active

        active = bool(local_only_active(None)) or bool(local_only_mode())
    except Exception:
        active = True
    if not active:
        return False
    try:
        from core.auto_local_only_mode import endpoint_is_local
    except Exception:
        return True
    for host in hosts:
        try:
            if not endpoint_is_local(f"imap://{host}"):
                return True
        except Exception:
            return True
    return False


def verify_imap_login(host: str, port: int, username: str, password: str) -> None:
    """Log in once and log out. Raises EmailSetupError with a kind; never returns server text.

    The connection is opened by ``email_tools._imap_connect``, the connector every mail read uses:
    implicit TLS or STARTTLS with ``ssl.create_default_context()`` (certificate and host name
    verified) and a 30 second timeout. A certificate that does not verify ends the attempt before
    the password is sent."""
    from core.email_tools import _imap_connect

    security = _security("imap", port)
    try:
        client = _imap_connect({"host": host, "port": port, "security": security})
    except ssl.SSLError:
        raise EmailSetupError("tls_failed") from None
    except TimeoutError:
        raise EmailSetupError("timeout") from None
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
    except ssl.SSLError:
        raise EmailSetupError("tls_failed") from None
    except TimeoutError:
        raise EmailSetupError("timeout") from None
    except OSError:
        raise EmailSetupError("cannot_reach") from None
    finally:
        with contextlib.suppress(Exception):
            client.logout()


# ---------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------


def _names(slot: str) -> tuple[str, str]:
    return f"email.imap.{slot}", f"email.smtp.{slot}"


def _oauth_owned(slot: str) -> bool:
    from core.credential_store import has_credential

    return any(has_credential(f"email.oauth.{provider}.{slot}") for provider in _OAUTH_PROVIDERS)


def _provider_of_entries(slot: str) -> set[str]:
    """The providers the slot's stored transport entries name (read inside strict reads)."""
    from core.credential_store import get_credential, strict_reads

    providers: set[str] = set()
    with strict_reads():
        for name in _names(slot):
            raw = get_credential(name)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            providers.add(str(data.get("provider") or "imap").strip().lower() if isinstance(data, dict) else "unreadable")
    return providers


def _write_entries(entries: dict[str, str | None], labels: dict[str, str]) -> None:
    """Apply {name: value or None (delete)}; on any failure put every touched name back as it was."""
    from core.credential_store import (
        CredentialReadError,
        delete_credential,
        get_credential,
        store_credential,
        strict_reads,
    )

    try:
        with strict_reads():
            previous = {name: get_credential(name) for name in entries}
    except CredentialReadError:
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
                    store_credential(name, str(previous[name]), label=labels.get(name, ""))
        raise EmailSetupError("store_failed", status=500) from None


def add_account(body: Any) -> dict[str, Any]:
    from core.credential_store import has_credential

    spec = _parse_add(body)
    slot = slot_for_address(spec["address"])
    hosts = [spec["imap_host"]] + ([spec["smtp_host"]] if spec["smtp_host"] else [])
    if _refused_by_local_only(hosts):
        raise EmailSetupError("local_only", status=403)
    imap_name, smtp_name = _names(slot)

    def _check_slot() -> None:
        if _oauth_owned(slot):
            raise EmailSetupError("managed_elsewhere", status=409)
        if has_credential(imap_name) or has_credential(smtp_name):
            if not spec["replace"]:
                raise EmailSetupError("exists", status=409)
            try:
                providers = _provider_of_entries(slot)
            except Exception:
                raise EmailSetupError("store_failed", status=500) from None
            if providers & set(_OAUTH_PROVIDERS):
                raise EmailSetupError("managed_elsewhere", status=409)

    _check_slot()  # refuse early: no login is attempted for a slot that would be refused anyway
    verify_imap_login(spec["imap_host"], spec["imap_port"], spec["username"], spec["password"])

    imap_blob = {"host": spec["imap_host"], "port": spec["imap_port"], "security": _security("imap", spec["imap_port"]),
                 "username": spec["username"], "password": spec["password"], "from_addr": spec["address"]}
    entries: dict[str, str | None] = {imap_name: json.dumps(imap_blob, sort_keys=True)}
    labels = {imap_name: f"Email reading · {spec['address']}", smtp_name: f"Email sending · {spec['address']}"}
    if spec["smtp_host"]:
        smtp_blob = {"host": spec["smtp_host"], "port": spec["smtp_port"], "security": _security("smtp", spec["smtp_port"]),
                     "username": spec["username"], "password": spec["password"], "from_addr": spec["address"]}
        entries[smtp_name] = json.dumps(smtp_blob, sort_keys=True)
    with _lock:
        _check_slot()  # the slot may have been taken while the login was in flight
        if not spec["smtp_host"] and has_credential(smtp_name):
            entries[smtp_name] = None  # a replaced account must not keep the old sending entry
        _write_entries(entries, labels)
    return {"ok": True, "account": slot, "address": spec["address"], "read": True, "send": bool(spec["smtp_host"]),
            "message": "The mail server accepted the login and the account is saved."}


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
            providers = _provider_of_entries(account)
        except Exception:
            raise EmailSetupError("store_failed", status=500) from None
        if providers & set(_OAUTH_PROVIDERS):
            raise EmailSetupError("managed_elsewhere", status=409)
        _write_entries({name: None for name in names}, {})
        if any(has_credential(name) for name in names):
            raise EmailSetupError("store_failed", status=500)
    return {"ok": True, "account": account, "message": "The account is removed from this machine."}


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


__all__ = ["MESSAGES", "EmailSetupError", "add_account", "handle", "remove_account", "slot_for_address", "verify_imap_login"]

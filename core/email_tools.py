"""Native local email: SMTP send/reply + IMAP read/search/thread, on the credential store + usage quota.

An account's settings live in the encrypted credential store as small JSON blobs:
`email.smtp.<account>` {"host","port","username","password","from_addr"} and
`email.imap.<account>` {"host","port","username","password"}. Both accept an optional
`"security"`: "ssl" (implicit TLS, the default — ports 465/993), "starttls" (RFC 3207/3501
upgrade — ports 587/143), or "plain" (loopback hosts ONLY: a disposable local test or a
trusted LAN relay; refused for anything not loopback, so a real account can never be silently
downgraded to cleartext). Sending is metered against the per-tier `email.send` quota (counted
only on success). Reading is read-only (EXAMINE + BODY.PEEK, never marks messages seen).

Delivery honesty: every sent message carries a Message-ID we generate BEFORE the wire, so a
receipt can name it. A connection that dies while the server may already have accepted DATA is
reported as `delivery_unknown` — never "failed", never auto-resent; `message_in_folder` is the
reconciliation lookup (search the account's own Sent folder for that Message-ID). Everything
fails closed (a structured result, never a raw exception).
"""
from __future__ import annotations

import contextlib
import email as email_lib
import email.utils
import imaplib
import ipaddress
import json
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

from core import credential_store, usage_quota


@dataclass
class EmailResult:
    ok: bool
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmailReadResult:
    ok: bool
    status: str
    message: str
    messages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class FolderLookupResult:
    ok: bool
    status: str
    message: str
    found: bool = False


def _load_account(kind: str, account: str) -> dict[str, Any] | None:
    raw = credential_store.get_credential(f"email.{kind}.{str(account or 'default').strip()}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _header_safe(value: Any) -> str:
    """Collapse CR/LF (and trim) out of a header value so it can neither inject extra headers nor
    raise a ValueError when assigned to an EmailMessage."""
    return " ".join(str(value or "").splitlines()).strip()


def _decoded_header(parsed: Any, name: str) -> str:
    """RFC 2047 decoded header text (`=?utf-8?Q?...?=` etc.) for every value we surface.

    `str(parsed.get(name))` returns the still-encoded wire form; a Unicode subject would be
    shown to the user (and matched by sender/subject search) as mojibake."""
    raw = parsed.get(name)
    if not raw:
        return ""
    try:
        from email.header import decode_header, make_header

        return str(make_header(decode_header(str(raw))))
    except Exception:
        return str(raw)


_ALLOWED_SECURITY_MODES = ("ssl", "starttls", "plain")


def _is_loopback_host(host: str) -> bool:
    """True only when `host` names loopback — by literal IP, by the exact `localhost`
    name, or by a hostname whose EVERY resolved address is loopback.

    Substring matching is a spoof (`localhost.attacker.example`); a name that fails to
    resolve is not loopback either. No network connection is made beyond the name lookup
    a connection would perform anyway."""
    text = str(host or "").strip()
    # tolerate a caller-supplied ":port" suffix ("127.0.0.1:0", "[::1]:993")
    if text.startswith("["):
        end = text.find("]")
        if end != -1 and text[end + 1 :].lstrip(":").isdigit():
            text = text[: end + 1]
    elif text.count(":") == 1:
        host_part, _, port_part = text.partition(":")
        if port_part.isdigit():
            text = host_part
    text = text.strip("[]").split("/")[0].strip()
    if not text:
        return False
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        pass
    if text.lower() == "localhost":
        return True
    try:
        infos = socket.getaddrinfo(text, None)
    except (OSError, UnicodeError):
        return False
    addresses = {info[4][0] for info in infos}
    try:
        return bool(addresses) and all(ipaddress.ip_address(a).is_loopback for a in addresses)
    except ValueError:
        return False


def _security_of(creds: dict[str, Any], host: str) -> str:
    """The account's transport security mode — STRICTLY allowlisted.

    An unknown value (a typo like 'starttIs') used to fall through to the cleartext
    constructor for any host; that is a silent downgrade, so it is refused before any
    socket is opened. 'plain' additionally requires an actually-loopback host — validated
    by identity, not substring — so a disposable local fixture may use cleartext while a
    real account never can."""
    raw = str(creds.get("security") or "ssl").strip().lower()
    if raw not in _ALLOWED_SECURITY_MODES:
        raise ValueError(
            f"unknown security mode {str(creds.get('security'))!r} for host {host!r}; "
            f"expected one of {', '.join(_ALLOWED_SECURITY_MODES)}"
        )
    if raw == "plain" and not _is_loopback_host(host):
        raise ValueError(f"security 'plain' is only permitted for loopback hosts (host={host!r})")
    return raw


def _smtp_connect(creds: dict[str, Any]) -> smtplib.SMTP:
    host = str(creds.get("host") or "")
    port = int(creds.get("port") or 465)
    security = _security_of(creds, host)
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
    client = smtplib.SMTP(host, port, timeout=30)
    if security == "starttls":
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    return client


def _imap_connect(creds: dict[str, Any]) -> imaplib.IMAP4:
    host = str(creds.get("host") or "")
    port = int(creds.get("port") or 993)
    security = _security_of(creds, host)
    if security == "ssl":
        return imaplib.IMAP4_SSL(host, port)
    client = imaplib.IMAP4(host, port)
    if security == "starttls":
        client.starttls(ssl.create_default_context())
    return client


def _profile_scope(principal: str | None, session_id: str) -> tuple[str, str, str]:
    """(principal, session, project) for a profile lookup: an explicit principal wins, else the
    turn in flight (bound by the front door), else the machine owner."""
    from core.operator_profile import OWNER_PRINCIPAL
    from core.operator_profile_turn import current_turn_scope

    bound_principal, bound_session, bound_project = current_turn_scope()
    who = str(principal or bound_principal or OWNER_PRINCIPAL)
    return who, str(session_id or bound_session or ""), bound_project


def resolve_default_account(kind: str = "smtp", *, principal: str | None = None, session_id: str = "") -> str:
    """The NAME of the account the operator chose for this transport (the account authority's
    selection, core.email_accounts.select_account), or '' when the selection is refused.

    The binding is the profile's OPAQUE credential reference (``credential:email.<smtp|imap>.<account>``
    or ``credential:email.oauth.<provider>.<account>``); whichever transport the reference spells, the
    account it names is the operator's mailbox for BOTH transports. Only the name is read, never a
    credential value. The legacy ``default`` slot is named only when nothing else was selected."""
    from core.email_accounts import select_account

    selection = select_account(kind, principal=principal, session_id=session_id)
    return selection.account if selection.ok else ""


def _selected_account(kind: str, account: str) -> tuple[str, dict[str, Any] | None, EmailReadResult | EmailResult | None]:
    """(account, its credentials for `kind`, None) for the account this transport should use, or
    ('', None, refusal) when no account can be selected: an explicit name that does not exist, a
    default that lacks the transport or its grant, two entries that are not one mailbox, or no chosen
    default among several. The refusal names the accounts that could serve; nothing is opened."""
    from core.email_accounts import select_account

    selection = select_account(kind, account=account)
    if not selection.ok:
        result_type = EmailReadResult if kind == "imap" else EmailResult
        return "", None, result_type(False, selection.status, selection.message)
    return selection.account, _load_account(kind, selection.account), None


def email_signature_for(*, principal: str | None = None, session_id: str = "") -> str:
    """The signature to append, from the profile (chat > project > work/personal > global)."""
    from core.operator_profile import resolve

    who, session, project = _profile_scope(principal, session_id)
    item = resolve(who, "email_signature", session_id=session, project_id=project, touch=True)
    return str(item.value_text) if item is not None else ""


def has_email_account(account: str = "default", *, kind: str = "smtp") -> bool:
    """True if SMTP (or IMAP) credentials are stored for the account."""
    return _load_account(kind, account) is not None


def _generate_message_id(from_addr: str) -> str:
    domain = str(from_addr or "").partition("@")[2] or "local"
    domain = _header_safe(domain) or "local"
    return email.utils.make_msgid(domain=domain)


def threading_headers(*, in_reply_to: str = "", references: list[str] | None = None) -> dict[str, str] | None:
    """Correct RFC 5322 threading headers for a reply.

    In-Reply-To names the message being answered; References carries the WHOLE
    ancestor chain (parent's references + parent's own id) so MUAs thread on
    any ancestor. All values pass through header injection sanitizing."""
    parent = _header_safe(in_reply_to)
    if not parent:
        return None
    chain: list[str] = []
    for ref in [*(references or []), parent]:
        clean = _header_safe(ref)
        if clean and clean not in chain:
            chain.append(clean)
    return {"In-Reply-To": parent, "References": " ".join(chain)}


def send_email(
    *,
    to: Any,
    subject: str,
    body: str,
    account: str = "",
    tier: str | None = None,
    extra_headers: dict[str, str] | None = None,
    signature: str = "",
    prepend_subject_prefix: bool = False,
    message_id_override: str = "",
    expected_identity: str = "",
) -> EmailResult:
    """Send an email for `account`. Metered by the per-tier `email.send` quota (counted only on a
    successful send). `signature`, if given, is appended to the body. Returns a structured result.

    An empty ``account`` means "the operator's default", resolved through the profile's opaque
    credential binding; the literal ``default`` names the ``default`` slot itself.

    Delivery honesty: the message gets a Message-ID we generate up front, returned in
    ``details["message_id"]``; a connection lost while the server may have already accepted the
    message is reported ``delivery_unknown`` (ok=True, NOT metered, never auto-retried) rather
    than ``send_failed``, because the two demand opposite follow-ups."""
    account, creds, refusal = _selected_account("smtp", str(account or ""))
    if refusal is not None:
        return refusal  # type: ignore[return-value]
    if creds is None:
        return EmailResult(
            False, "needs_credentials",
            f"No SMTP credentials stored for account '{account}'. I can send once this account is "
            "configured: store its host, port, username and an app password via the runtime's "
            "credential setup (ask me to open email account setup). I will never ask you to paste "
            "a password into chat." + _configured_choices("smtp", account),
        )
    recipients = [to] if isinstance(to, str) else [str(r) for r in (to or [])]
    recipients = [r.strip() for r in recipients if str(r).strip()]
    if not recipients:
        return EmailResult(False, "invalid_recipient", "No recipient address was provided.")

    tier = tier or usage_quota.active_tier()
    check = usage_quota.check_quota("email.send", tier=tier)
    if not check.allowed:
        return EmailResult(
            False, "quota_exceeded",
            f"Email send limit reached for the '{tier}' tier ({check.used}/{check.limit} today).",
            details={"used": check.used, "limit": check.limit},
        )

    subj = str(subject or "")
    if prepend_subject_prefix and subj and not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"
    full_body = str(body or "")
    if str(signature or "").strip():
        full_body = f"{full_body}\n\n{str(signature).strip()}"
    # The draft flow reserves its Message-ID BEFORE dispatch (the durable send
    # reservation), so the wire message must carry exactly that id — never a fresh
    # one that the reservation cannot reconcile.
    message_id = (_header_safe(message_id_override)
                  or _generate_message_id(creds.get("from_addr") or creds.get("username") or ""))
    try:
        message = EmailMessage()
        message["From"] = _header_safe(creds.get("from_addr") or creds.get("username") or "")
        message["To"] = _header_safe(", ".join(recipients))
        message["Subject"] = _header_safe(subj)
        message["Message-ID"] = _header_safe(message_id)
        for key, value in (extra_headers or {}).items():
            clean = _header_safe(value)
            if clean:
                message[str(key)] = clean
        message.set_content(full_body)
    except (ValueError, TypeError) as exc:  # illegal header content -> fail closed, never raise
        return EmailResult(False, "invalid_message", f"Email not sent: could not build the message ({exc}).")

    # Provider transports (Gmail/Graph): the SAME canonical message, their API. The
    # reviewed parent RFC id rides along so the adapter can bind the reply to the
    # ACTUAL parent (Gmail threadId; Graph checks the MIME binding and looks the parent up).
    try:
        adapter = _provider_adapter(account, creds)
    except Exception as exc:
        return _provider_error_result(exc)
    if adapter is not None:
        reply_to_rfc = str((extra_headers or {}).get("In-Reply-To") or "").strip()
        # Every provider send is bound to a mailbox: the approval's identity when a
        # reviewed draft is sent, otherwise the mailbox this account slot is
        # configured for. The adapter proves it under the dispatching credential.
        bound_identity = str(expected_identity or "") or configured_identity(account, creds)
        try:
            return adapter.send(raw_mime=message.as_bytes(), thread_id="", reply_to_rfc=reply_to_rfc,
                                expected_identity=bound_identity)
        except Exception as exc:
            return _provider_error_result(exc)
    username = str(creds.get("username") or "")
    password = str(creds.get("password") or "")
    try:
        try:
            port = int(creds.get("port") or 465)
        except (TypeError, ValueError):
            raise OSError(f"invalid port value {creds.get('port')!r}") from None
        server = _smtp_connect({**creds, "port": port})
    except Exception as exc:  # connect / starttls / config — nothing was handed to the wire
        return EmailResult(False, "send_failed", f"Email send failed ({type(exc).__name__}): {exc}",
                           details={"message_id": message_id})
    try:
        if username and password:
            server.login(username, password)
        server.send_message(message)
    except (TimeoutError, smtplib.SMTPServerDisconnected, ConnectionError, ssl.SSLError) as exc:
        # The connection died around the DATA exchange. The server may already have accepted
        # and queued the message — SMTP has no exactly-once handshake — so this is UNKNOWN
        # delivery, not failure: never auto-resend, reconcile via the Sent folder instead.
        # NOTE the order: SMTPServerDisconnected SUBCLASSES SMTPResponseException, so this
        # clause must come first — a silent close while reading the reply is NOT a spoken
        # 4xx/5xx, and classifying it as "failed" invites exactly the blind resend the
        # unknown state exists to prevent.
        _ = exc
        return EmailResult(
            True, "delivery_unknown",
            "The mail server connection dropped right after (or while) accepting the message, so "
            "delivery is UNKNOWN: it may have been sent. I have not resent it and will not guess. "
            f"Reconcile via the account's Sent folder using Message-ID {message_id}.",
            details={"message_id": message_id},
        )
    except smtplib.SMTPResponseException as exc:
        # The server SPOKE: a real, final answer (4xx/5xx). Definitively not delivered.
        return EmailResult(False, "send_failed", f"Email send failed ({type(exc).__name__}): {exc}",
                           details={"message_id": message_id, "smtp_code": getattr(exc, "smtp_code", None)})
    except OSError as exc:
        # Socket-level failure while sending (e.g. broken pipe mid-DATA): the server may or may
        # not have consumed the full payload — unknown, same law as above.
        _ = exc
        return EmailResult(
            True, "delivery_unknown",
            "The mail server connection dropped right after (or while) accepting the message, so "
            "delivery is UNKNOWN: it may have been sent. I have not resent it and will not guess. "
            f"Reconcile via the account's Sent folder using Message-ID {message_id}.",
            details={"message_id": message_id},
        )
    finally:
        with contextlib.suppress(Exception):
            server.quit()

    usage_quota.consume_quota("email.send", tier=tier)  # count only a real, successful send
    return EmailResult(
        True, "executed", f"Email sent to {', '.join(recipients)}.",
        details={"to": recipients, "subject": subj, "message_id": message_id},
    )


def reply_email(
    *, to: Any, subject: str, body: str, account: str = "", in_reply_to: str = "",
    references: list[str] | None = None, tier: str | None = None,
    session_id: str = "",
) -> EmailResult:
    """Reply to a message: prefixes 'Re:' if absent, threads via In-Reply-To/References, and appends
    the operator's email signature from the profile. Delegates to send_email (same quota + gating).

    `references` is the parent's full ancestor chain when known (see `open_thread`); the reply's
    References header becomes parent-chain + parent-id, per RFC 5322."""
    try:
        signature = email_signature_for(session_id=session_id)
    except Exception:
        signature = ""
    headers = threading_headers(in_reply_to=in_reply_to, references=references)
    return send_email(
        to=to, subject=subject, body=body, account=account, tier=tier, extra_headers=headers,
        signature=signature, prepend_subject_prefix=True,
    )


def _first_message_bytes(fetched: Any) -> bytes | None:
    for part in fetched or []:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            return bytes(part[1])
    return None


def _body_text(parsed: Any, limit: int | None = None) -> str:
    """Full decoded text of a message: text/plain preferred, HTML stripped as a fallback.

    Multipart/alternative carries the same content in both parts, so the plain one is the
    faithful rendering; an HTML-only message is reduced to its visible text (tags dropped),
    which is honest for summarizing and replying, and `limit` (when given) truncates."""
    try:
        html_fallback = ""
        for part in parsed.walk():
            if part.get_filename():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                text = part.get_payload(decode=True) or b""
                value = text.decode(part.get_content_charset() or "utf-8", "replace").strip()
                return value[:limit] if limit else value
            if ctype == "text/html" and not html_fallback:
                text = part.get_payload(decode=True) or b""
                html_fallback = text.decode(part.get_content_charset() or "utf-8", "replace")
        if html_fallback:
            import re

            text = re.sub(r"<[^>]+>", " ", html_fallback)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:limit] if limit else text
        if not parsed.is_multipart():
            text = parsed.get_payload(decode=True) or b""
            value = text.decode(parsed.get_content_charset() or "utf-8", "replace").strip()
            return value[:limit] if limit else value
    except Exception:
        return ""
    return ""


def _text_snippet(parsed: Any, limit: int = 240) -> str:
    return _body_text(parsed, limit=limit)


def _summary_from_parsed(parsed: Any) -> dict[str, str]:
    return {
        "from": _decoded_header(parsed, "From"),
        "to": _decoded_header(parsed, "To"),
        "subject": _decoded_header(parsed, "Subject"),
        "date": str(parsed.get("Date") or ""),
        "message_id": str(parsed.get("Message-ID") or "").strip(),
        "snippet": _text_snippet(parsed),
    }


def _imap_date(value: str) -> str:
    """'2026-09-10' or ISO-ish -> IMAP SEARCH '10-Sep-2026'. Time is not part of DATE searches."""
    import datetime as _dt

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = _dt.date.fromisoformat(text[:10])
        return parsed.strftime("%d-%b-%Y")
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
            return parsed.strftime("%d-%b-%Y")
        except (TypeError, ValueError):
            return ""


def configured_identity(account: str, creds: dict[str, Any] | None) -> str:
    """The LOCAL mailbox binding an account slot names: its outward sending address
    plus, for OAuth providers, the principal its handle's metadata names, as
    `alias|oauth-meta:principal` (lowercased; never a secret).

    This is the string an approval binds and a reservation records. It is NOT
    proof of authentication: provider transports verify the principal it names
    against the provider's own profile under the exact credential that carries
    each bound request (core.email_providers.base.PrincipalBinding)."""
    from core.email_providers.base import _load_oauth_handle, provider_for_account

    if not isinstance(creds, dict):
        return ""
    alias = str(creds.get("from_addr") or creds.get("username") or "").strip().lower()
    try:
        provider = provider_for_account(creds)
    except Exception:
        provider = "imap"
    if provider not in ("gmail", "graph"):
        return alias
    handle = creds.get("_oauth_handle")
    if not isinstance(handle, dict) or not handle:
        handle = _load_oauth_handle(provider, account)
    principal = str((handle or {}).get("account_email") or "").strip().lower()
    return f"{alias}|oauth-meta:{principal}" if principal else alias


def _provider_adapter(account: str, creds: dict[str, Any] | None):
    """The provider transport for an account, or None for the standards path.

    Raises ProviderAccountError for unknown providers (never a silent fallback
    to password SMTP). API bases are read from the credential blob when present
    (`api_base`/`token_url`) so recorded-shape contract tests can point the
    adapter at a local server; production defaults are the real endpoints."""
    from core.email_providers.base import provider_for_account

    provider = provider_for_account(creds)
    if provider in ("imap", "icloud"):
        return None
    blob = dict(creds or {})
    api_base = str(blob.pop("api_base", "") or "")
    token_url = str(blob.pop("token_url", "") or "")
    kwargs = {"account": account, "creds": blob}
    if api_base:
        kwargs["api_base"] = api_base
    if token_url:
        kwargs["token_url"] = token_url
    if provider == "gmail":
        from core.email_providers.gmail import GmailAdapter

        return GmailAdapter(**kwargs)
    from core.email_providers.graph import GraphAdapter

    return GraphAdapter(**kwargs)


def _provider_error_text(exc) -> str:
    from core.email_providers.base import ProviderAccountError

    if isinstance(exc, ProviderAccountError):
        if exc.kind == "needs_reauthorization":
            return (f"Email account needs reauthorization: {exc}. Use email account setup to reconnect; "
                    "no password is ever requested in chat.")
        if exc.kind == "needs_setup":
            return f"Email account is not connected yet: {exc}"
        return f"Email provider call failed: {exc}"
    return f"Email provider error ({type(exc).__name__}): {exc}"


def _provider_error_result(exc) -> EmailResult:
    from core.email_providers.base import ProviderAccountError

    status = exc.kind if isinstance(exc, ProviderAccountError) else "read_failed"
    return EmailResult(False, status, _provider_error_text(exc))


def _provider_read_error(exc) -> EmailReadResult:
    from core.email_providers.base import ProviderAccountError

    status = exc.kind if isinstance(exc, ProviderAccountError) else "read_failed"
    return EmailReadResult(False, status, _provider_error_text(exc))


def _configured_choices(kind: str, account: str) -> str:
    """The configured accounts that could serve `kind` instead of `account`, as a sentence (or '')."""
    try:
        from core.email_accounts import _capable, configured_accounts

        choices = [name for name in _capable(configured_accounts(), kind) if name != account]
    except Exception:
        choices = []
    if not choices:
        return ""
    return f" Accounts that can {'read' if kind == 'imap' else 'send'}: {', '.join(choices)}."


def _needs_credentials(kind: str, account: str) -> EmailReadResult | EmailResult | None:
    if _load_account(kind, account) is None:
        if kind == "imap":
            return EmailReadResult(
                False, "needs_credentials",
                f"No IMAP credentials stored for account '{account}'. I can read once this account "
                "is configured: store its host, port, username and password via the runtime's "
                "credential setup (ask me to open email account setup). I will never ask you to "
                "paste a password into chat." + _configured_choices(kind, account),
            )
        return EmailResult(False, "needs_credentials",
                           f"No SMTP credentials stored for account '{account}'." + _configured_choices(kind, account))
    return None


_SEARCH_SCAN_CAP = 2000
_SEARCH_PAGE = 50


@dataclass(frozen=True)
class MailSearch:
    """One provider-account search's typed filters. The adapter expresses them in its own provider's
    documented query syntax, or refuses what that syntax cannot express; it never borrows another
    provider's syntax and never drops a filter. Dates are YYYY-MM-DD; empty means unset."""

    sender: str = ""
    subject: str = ""
    since: str = ""
    before: str = ""
    unseen_only: bool = False
    seen_only: bool = False


def _search_day(value: str) -> str | None:
    """A typed date filter as YYYY-MM-DD, read as the IMAP path reads it (_imap_date), or None."""
    import datetime as _dt

    text = str(value or "").strip()
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        try:
            return email.utils.parsedate_to_datetime(text).date().isoformat()
        except (TypeError, ValueError):
            return None


def _provider_search_request(*, folder: str, sender: str, subject: str, since: str, before: str,
                             unseen_only: bool, seen_only: bool) -> MailSearch | EmailReadResult:
    """The typed filters of a provider-account search, or the refusal to return before any request.

    A provider search (Gmail's q, Graph's $search or $filter) covers the whole mailbox, so a folder
    other than the inbox is refused instead of ignored; a date that is not a date and contradictory
    read states are refused instead of dropped."""
    if str(folder or "INBOX").strip().upper() != "INBOX":
        return EmailReadResult(False, "unsupported_folder",
                               f"This account's provider search covers the whole mailbox and cannot be "
                               f"limited to the '{folder}' folder, so nothing was searched.")
    days: dict[str, str] = {}
    for name, value in (("since", since), ("before", before)):
        if not str(value or "").strip():
            continue
        day = _search_day(value)
        if day is None:
            return EmailReadResult(False, "invalid_arguments",
                                   f"'{name}' must be a date (YYYY-MM-DD), not {str(value)!r}, so nothing "
                                   "was searched.")
        days[name] = day
    if unseen_only and seen_only:
        return EmailReadResult(False, "invalid_arguments",
                               "unseen_only and seen_only contradict each other, so nothing was searched.")
    return MailSearch(sender=str(sender or ""), subject=str(subject or ""), since=days.get("since", ""),
                      before=days.get("before", ""), unseen_only=bool(unseen_only), seen_only=bool(seen_only))


def search_email(
    *,
    account: str = "",
    folder: str = "INBOX",
    sender: str = "",
    subject: str = "",
    since: str = "",
    before: str = "",
    unseen_only: bool = False,
    seen_only: bool = False,
    limit: int = 25,
) -> EmailReadResult:
    """Search a folder by sender/subject substrings, a date window and/or unread state.

    Read-only (EXAMINE + BODY.PEEK). Server-side IMAP SEARCH carries the date window and the
    flags; header substrings are refined CLIENT-side on decoded headers, because server
    substring matching over RFC 2047-encoded headers is unreliable.

    SEARCH BEFORE LIMIT: the refinement walk starts from the NEWEST server-matching id and
    pages backwards through ALL of them until `limit` matches are found — a match is never
    missed because unrelated newer messages crowded it out. The walk is bounded by
    _SEARCH_SCAN_CAP fetched messages per search; if that bound is hit before `limit`
    matches, the result SAYS SO (`scan_bounded`) instead of reporting a confident count.
    Never raises."""
    account, creds, refusal = _selected_account("imap", str(account or ""))
    if refusal is not None:
        return refusal  # type: ignore[return-value]
    # Provider transports (Gmail/Graph) carry their own search: each adapter expresses
    # the typed filters in its provider's documented syntax (or refuses what it cannot),
    # and their summaries flow through the same EmailReadResult contract the standards
    # path uses.
    try:
        adapter = _provider_adapter(account, creds)
    except Exception as exc:  # unknown provider, a missing grant — structured, never a fallback
        # A READ answers with a read result: the executor reads `.messages` off it. Measured served
        # 2026-09-14 ("check my personal inbox" after the personal grant was removed): the adapter's typed
        # needs_setup came back as an EmailResult and email.read failed on `.messages` instead of refusing.
        return _provider_read_error(exc)
    if adapter is not None:
        request = _provider_search_request(folder=folder, sender=sender, subject=subject, since=since,
                                           before=before, unseen_only=unseen_only, seen_only=seen_only)
        if isinstance(request, EmailReadResult):
            return request
        try:
            return adapter.search(request, limit=limit)
        except Exception as exc:
            return _provider_read_error(exc)
    missing = _needs_credentials("imap", account)
    if missing is not None:
        return missing  # type: ignore[return-value]
    username = str(creds.get("username") or "")
    password = str(creds.get("password") or "")
    folder = str(folder or "INBOX")
    messages: list[dict[str, str]] = []
    scan_bounded = False
    scanned = 0
    criteria: list[str] = []
    if unseen_only:
        criteria.append("UNSEEN")
    if seen_only:
        criteria.append("SEEN")
    since_imap = _imap_date(since)
    before_imap = _imap_date(before)
    if since_imap:
        criteria += ["SINCE", since_imap]
    if before_imap:
        criteria += ["BEFORE", before_imap]
    sender_l = str(sender or "").strip().lower()
    subject_l = str(subject or "").strip().lower()
    header_filter = bool(sender_l or subject_l)
    try:
        limit = max(1, min(100, int(limit or 25)))
        with _imap_connect(creds) as imap:
            imap.login(username, password)
            imap.select(folder, readonly=True)
            _typ, data = imap.search(None, *criteria) if criteria else imap.search(None, "ALL")
            ids = data[0].split() if data and data[0] else []
            # No header filter: the server already matched everything; newest N is exact.
            walk_ids = list(reversed(ids)) if header_filter else list(reversed(ids[-limit:]))
            for start in range(0, len(walk_ids), _SEARCH_PAGE):
                page = walk_ids[start : start + _SEARCH_PAGE]
                for mid in page:
                    if scanned >= _SEARCH_SCAN_CAP:
                        scan_bounded = True
                        break
                    _t, fetched = imap.fetch(mid, "(BODY.PEEK[])")
                    raw = _first_message_bytes(fetched)
                    if raw is None:
                        continue
                    scanned += 1
                    parsed = email_lib.message_from_bytes(raw)
                    if sender_l and sender_l not in _decoded_header(parsed, "From").lower():
                        continue
                    if subject_l and subject_l not in _decoded_header(parsed, "Subject").lower():
                        continue
                    entry = _summary_from_parsed(parsed)
                    entry["seq"] = mid.decode("latin-1", "replace")
                    messages.append(entry)
                if len(messages) >= limit or scan_bounded:
                    break
    except Exception as exc:  # network / auth / config
        return EmailReadResult(False, "read_failed", f"Email search failed ({type(exc).__name__}): {exc}")
    summary = (
        f"{len(messages)} message(s) matched in {folder}"
        + f" (from {sender!r})" * bool(sender)
        + f" (subject {subject!r})" * bool(subject)
        + f" (since {since})" * bool(since)
        + f" (before {before})" * bool(before)
        + " (unread only)" * bool(unseen_only)
        + "."
    )
    if scan_bounded and len(messages) < limit:
        summary += (
            f" Search bounded: {scanned} message(s) scanned (cap {_SEARCH_SCAN_CAP}) before "
            f"finding {limit}; older matches may exist."
        )
    return EmailReadResult(True, "executed", summary, messages=messages)


def _fetch_all(creds: dict[str, Any], folder: str, cap: int = 500) -> tuple[list[tuple[str, bytes]], int]:
    """Newest-first (sequence number descending) PEEK fetch of a folder's messages.

    Returns (messages, folder_total): the walk is capped at `cap` NEWEST messages, and the
    caller can (and must) surface the difference, so a message not found behind the cap is
    reported as bounded incompleteness rather than confident absence."""
    with _imap_connect(creds) as imap:
        imap.login(str(creds.get("username") or ""), str(creds.get("password") or ""))
        imap.select(folder, readonly=True)
        _typ, data = imap.search(None, "ALL")
        all_ids = data[0].split() if data and data[0] else []
        ids = all_ids[-cap:]
        raws: list[tuple[str, bytes]] = []
        for mid in reversed(ids):
            _t, fetched = imap.fetch(mid, "(BODY.PEEK[])")
            raw = _first_message_bytes(fetched)
            if raw is not None:
                raws.append((mid.decode("latin-1", "replace"), raw))
    return raws, len(all_ids)


def _references_of(parsed: Any) -> list[str]:
    refs = str(parsed.get("References") or "").split()
    in_reply_to = str(parsed.get("In-Reply-To") or "").strip()
    chain = [r for r in refs if r.strip()]
    if in_reply_to and in_reply_to not in chain:
        chain.append(in_reply_to)
    return chain


def open_message(*, account: str = "", folder: str = "INBOX", message_id: str) -> EmailReadResult:
    """Open ONE message with FULL content by its Message-ID (read-only).

    This is the 'open the email and read it' step: the whole body, decoded headers, and the
    threading headers a grounded reply needs (In-Reply-To / References / Reply-To)."""
    wanted = str(message_id or "").strip()
    if not wanted:
        return EmailReadResult(False, "missing_message_id", "No message id given to open.")
    account, creds, refusal = _selected_account("imap", str(account or ""))
    if refusal is not None:
        return refusal  # type: ignore[return-value]
    try:
        adapter = _provider_adapter(account, creds)
    except Exception as exc:
        return _provider_read_error(exc)
    if adapter is not None:
        try:
            return adapter.open_message(provider_id=wanted)
        except Exception as exc:
            return _provider_read_error(exc)
    missing = _needs_credentials("imap", account)
    if missing is not None:
        return missing  # type: ignore[return-value]
    creds = _load_account("imap", account)
    try:
        raws, folder_total = _fetch_all(creds, str(folder or "INBOX"))
        bounded = folder_total > len(raws)
        for seq, raw in raws:
            parsed = email_lib.message_from_bytes(raw)
            if str(parsed.get("Message-ID") or "").strip() == wanted:
                entry = _summary_from_parsed(parsed)
                entry["seq"] = seq
                entry["body"] = _body_text(parsed)
                entry["in_reply_to"] = str(parsed.get("In-Reply-To") or "").strip()
                entry["references"] = " ".join(_references_of(parsed))
                entry["reply_to"] = _decoded_header(parsed, "Reply-To") or entry["from"]
                return EmailReadResult(True, "executed", f"Opened message {wanted}.", messages=[entry])
    except Exception as exc:
        return EmailReadResult(False, "read_failed", f"Email open failed ({type(exc).__name__}): {exc}")
    return EmailReadResult(
        False, "not_found",
        f"No message with Message-ID {wanted} in {folder} of account '{account}'."
        + (f" (Search bounded: scanned the newest {len(raws)} of {folder_total} messages.)" if bounded else ""),
    )


def open_thread(*, account: str = "", folder: str = "INBOX", message_id: str, limit: int = 50) -> EmailReadResult:
    """Open the WHOLE thread containing `message_id`: the transitive closure over
    In-Reply-To/References within the folder, oldest first, full bodies.

    A reply grounded in the thread answers the LATEST message (highest sequence in the chain)
    with the complete References chain, not a snippet guess."""
    anchor = str(message_id or "").strip()
    if not anchor:
        return EmailReadResult(False, "missing_message_id", "No message id given to open a thread.")
    account, creds, refusal = _selected_account("imap", str(account or ""))
    if refusal is not None:
        return refusal  # type: ignore[return-value]
    try:
        adapter = _provider_adapter(account, creds)
    except Exception as exc:
        return _provider_read_error(exc)
    if adapter is not None:
        # Resolve the anchor message first, then its provider thread.
        try:
            opened = adapter.open_message(provider_id=anchor)
        except Exception as exc:
            return _provider_read_error(exc)
        if not opened.ok:
            return opened
        thread_ref = opened.messages[0].get("thread_id") or opened.messages[0].get("conversation_id") or ""
        if not thread_ref:
            return opened
        try:
            return adapter.open_thread(thread_id=thread_ref)
        except Exception as exc:
            return _provider_read_error(exc)
    missing = _needs_credentials("imap", account)
    if missing is not None:
        return missing  # type: ignore[return-value]
    creds = _load_account("imap", account)
    try:
        raws, _folder_total = _fetch_all(creds, str(folder or "INBOX"))
    except Exception as exc:
        return EmailReadResult(False, "read_failed", f"Email thread open failed ({type(exc).__name__}): {exc}")
    parsed_by_id: dict[str, tuple[str, Any]] = {}
    for seq, raw in raws:
        parsed = email_lib.message_from_bytes(raw)
        mid = str(parsed.get("Message-ID") or "").strip()
        if mid:
            parsed_by_id[mid] = (seq, parsed)
    if anchor not in parsed_by_id:
        return EmailReadResult(
            False, "not_found",
            f"No message with Message-ID {anchor} in {folder} of account '{account}'.",
        )
    # Closure: a message belongs to the thread if its id or any of its ancestors/descendants
    # is reachable from the anchor through the References/In-Reply-To graph (both directions).
    related: set[str] = set()
    frontier = [anchor]
    while frontier:
        current = frontier.pop()
        if current in related:
            continue
        related.add(current)
        _seq, parsed = parsed_by_id[current]
        for parent in _references_of(parsed):
            if parent in parsed_by_id and parent not in related:
                frontier.append(parent)
        for mid, (_s, other) in parsed_by_id.items():
            if mid not in related and current in _references_of(other):
                frontier.append(mid)
    def _date_key(mid: str) -> tuple[int, str]:
        seq, parsed = parsed_by_id[mid]
        try:
            when = email.utils.parsedate_to_datetime(str(parsed.get("Date") or ""))
            stamp = when.timestamp() if when else 0
        except (TypeError, ValueError, OverflowError):
            stamp = 0
        return (int(stamp), seq)

    messages: list[dict[str, str]] = []
    for mid in sorted(related, key=_date_key):
        seq, parsed = parsed_by_id[mid]
        entry = _summary_from_parsed(parsed)
        entry["seq"] = seq
        entry["body"] = _body_text(parsed)
        entry["in_reply_to"] = str(parsed.get("In-Reply-To") or "").strip()
        entry["references"] = " ".join(_references_of(parsed))
        entry["reply_to"] = _decoded_header(parsed, "Reply-To") or entry["from"]
        messages.append(entry)
    latest = messages[-1]
    return EmailReadResult(
        True, "executed",
        f"Opened thread of {len(messages)} message(s) anchored at {anchor}; "
        f"latest is {latest['message_id']}.",
        messages=messages,
    )


def message_in_folder(*, account: str = "", folder: str = "Sent", message_id: str,
                      subject: str = "", recipient: str = "", recipients: list[str] | None = None,
                      since_epoch: float = 0.0, expected_identity: str = "") -> FolderLookupResult:
    """Does `folder` on `account` hold exactly this Message-ID? The delivery-reconciliation
    lookup for an unknown-outcome send: finding OUR generated id in the account's own Sent
    copy proves the server accepted it. Read-only; never raises.

    Provider lookups are bound to `expected_identity` (the reservation's mailbox;
    the slot's configured mailbox when none is given): a credential that cannot
    be proven to reach it yields that refusal as the status, never an absence."""
    wanted = str(message_id or "").strip()
    if not wanted:
        return FolderLookupResult(False, "missing_message_id", "No message id given to look up.")
    account, creds, refusal = _selected_account("imap", str(account or ""))
    if refusal is not None:
        # The reconciliation lookup carries the selection's refusal as its status: an account that
        # cannot be read is not an absent message.
        return FolderLookupResult(False, refusal.status, refusal.message)
    try:
        adapter = _provider_adapter(account, creds)
    except Exception:
        adapter = None
    if adapter is not None:
        from core.email_providers.base import BINDING_REFUSALS, ProviderAccountError

        bound_identity = str(expected_identity or "") or configured_identity(account, creds)
        try:
            outcome = adapter.message_in_sent(message_id=message_id, subject=subject,
                                              recipient=recipient, recipients=list(recipients or []),
                                              since_epoch=since_epoch, expected_identity=bound_identity)
            if isinstance(outcome, tuple):
                found, note = outcome
            else:
                found, note = outcome, ("is" if outcome else "is not") + " in the provider's sent view"
            return FolderLookupResult(True, "found" if found else "absent",
                                      f"Reconciliation: {note}.", found=found)
        except ProviderAccountError as exc:
            if exc.kind in BINDING_REFUSALS:
                # Evidence from an unproven or different mailbox is no evidence: the
                # refusal is the result, never an "absent" lookup.
                return FolderLookupResult(False, exc.kind, f"Reconciliation refused: {exc}")
            return FolderLookupResult(False, "provider_failed",
                                      f"Provider reconciliation failed ({type(exc).__name__}): {exc}")
        except Exception as exc:
            return FolderLookupResult(False, "provider_failed",
                                      f"Provider reconciliation failed ({type(exc).__name__}): {exc}")
    if creds is None:
        return FolderLookupResult(
            False, "needs_credentials",
            f"No IMAP credentials for account '{account}'; cannot reconcile from the Sent folder.",
        )
    try:
        with _imap_connect(creds) as imap:
            imap.login(str(creds.get("username") or ""), str(creds.get("password") or ""))
            status, _data = imap.select(folder, readonly=True)
            if status != "OK":
                return FolderLookupResult(
                    False, "no_folder",
                    f"Folder '{folder}' is not visible on account '{account}'; cannot reconcile.",
                )
            _typ, data = imap.search(None, "HEADER", "MESSAGE-ID", f'"{wanted}"')
            ids = (data[0].split() if data and data[0] else [])
            for mid in ids:
                _t, fetched = imap.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                raw = _first_message_bytes(fetched)
                if raw is None:
                    continue
                parsed = email_lib.message_from_bytes(raw)
                if str(parsed.get("Message-ID") or "").strip() == wanted:
                    return FolderLookupResult(True, "found", f"{wanted} is present in {folder}.", found=True)
    except Exception as exc:
        return FolderLookupResult(False, "read_failed", f"Sent-folder lookup failed ({type(exc).__name__}): {exc}")
    return FolderLookupResult(True, "absent", f"{wanted} was not found in {folder}.", found=False)


def read_email(*, account: str = "", folder: str = "INBOX", limit: int = 10) -> EmailReadResult:
    """Fetch the most recent messages (headers + a text snippet), read-only (BODY.PEEK). Never raises."""
    return search_email(account=account, folder=folder, limit=limit)


__all__ = [
    "EmailReadResult",
    "EmailResult",
    "FolderLookupResult",
    "email_signature_for",
    "has_email_account",
    "message_in_folder",
    "open_message",
    "open_thread",
    "read_email",
    "reply_email",
    "resolve_default_account",
    "search_email",
    "send_email",
    "threading_headers",
]

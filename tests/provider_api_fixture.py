"""A local recorded-shape server for the OAuth2 + Gmail + Graph mail contracts.

Real HTTP sockets on loopback (ephemeral ports). The JSON shapes follow the
providers' documented REST contracts so the adapters are exercised exactly as
they will speak to the live endpoints — minus the live credential, which is the
labelled acceptance gate. NOT pytest tests; infrastructure.

Chaos hook: `drop_after_accept` closes the connection after a sendMail/messages
send is ACCEPTED but before the 2xx reply is written (the unknown-delivery case).
"""
from __future__ import annotations

import base64
import contextlib
import json
import re
import threading
import time
import urllib.parse
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.oauth: dict[tuple[str, str], dict[str, Any]] = {}
        self.access_tokens: dict[str, dict[str, Any]] = {}
        self.gmail_mailboxes: dict[str, list[dict[str, Any]]] = {}
        self.gmail_sent: dict[str, list[dict[str, Any]]] = {}
        self.graph_mailboxes: dict[str, list[dict[str, Any]]] = {}
        self.graph_sent: dict[str, list[dict[str, Any]]] = {}
        self.graph_replies: dict[str, list[dict[str, Any]]] = {}
        self.drop_after_accept = False
        self.reject_next_send = False
        self.principal_overrides: dict[tuple[tuple[str, str], str], str] = {}
        # A token authenticates the principal it was MINTED for. A schedule makes
        # successive mints for one account authenticate successive principals (a
        # grant switched between two refreshes); the last entry repeats.
        self.principal_schedule: dict[tuple[str, str], list[str]] = {}
        self.mint_counts: dict[tuple[str, str], int] = {}
        # Profile read failure per account: "forbidden" (403), "missing" (200
        # without identity fields), "drop" (connection closed before a status).
        self.profile_failures: dict[tuple[str, str], str] = {}
        self.graph_mail_null: set[tuple[str, str]] = set()
        # Every authenticated API request as the provider saw it: method, path,
        # bearer token, the principal that token authenticates, response status.
        self.request_log: list[dict[str, Any]] = []
        # Server-side access revocation (401 for a well-formed token): a rule either
        # revokes the token of the next matching request (that token stays refused, a
        # freshly minted one works) or refuses every token on matching requests.
        self.revocation_rules: list[dict[str, Any]] = []
        self.revoked_tokens: set[str] = set()
        # Token endpoint evidence: every mint and refusal per account, and an optional
        # cap on successful mints after which the grant is refused (invalid_grant).
        self.token_log: list[dict[str, Any]] = []
        self.total_mints: dict[tuple[str, str], int] = {}
        self.refresh_mint_limit: dict[tuple[str, str], int] = {}
        self._seq = 0

    def _index_gmail_sent(self, address: str, payload: dict[str, Any]) -> None:
        import email as email_lib

        raw_b64 = str(payload.get("raw") or "")
        raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
        parsed = email_lib.message_from_bytes(raw)
        entry = {
            "from": str(parsed.get("From") or address),
            "to": str(parsed.get("To") or ""),
            "subject": str(parsed.get("Subject") or ""),
            "body": parsed.get_payload() if not parsed.is_multipart() else "",
            "message_id": str(parsed.get("Message-ID") or ""),
            "date": str(parsed.get("Date") or ""),
            "provider_id": self.next_id("gm-sentidx-"),
            "thread_id": str(payload.get("threadId") or self.next_id("th-")),
        }
        self.gmail_mailboxes.setdefault(address, []).append(_gmail_message_model(entry, self, entry["thread_id"]))

    def next_id(self, prefix: str) -> str:
        with self.lock:
            self._seq += 1
            return f"{prefix}{self._seq:04d}"

    def mint_principal(self, key: tuple[str, str], grant: str, record: dict[str, Any]) -> str:
        """The principal a token minted NOW for this account and grant authenticates."""
        with self.lock:
            override = self.principal_overrides.get((key, grant))
            if override:
                return override
            schedule = self.principal_schedule.get(key)
            if schedule:
                index = self.mint_counts.get(key, 0)
                self.mint_counts[key] = index + 1
                return schedule[min(index, len(schedule) - 1)]
            return str(record.get("account_email") or key[1] or "")


def _gmail_message_model(entry: dict[str, Any], state: _State, thread_id: str) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": entry["from"]},
        {"name": "To", "value": entry.get("to", "me")},
        {"name": "Subject", "value": entry["subject"]},
        {"name": "Date", "value": entry["date"]},
        {"name": "Message-ID", "value": entry["message_id"]},
    ]
    if entry.get("in_reply_to"):
        headers.append({"name": "In-Reply-To", "value": entry["in_reply_to"]})
    if entry.get("references"):
        headers.append({"name": "References", "value": entry["references"]})
    return {
        "id": entry.get("provider_id") or state.next_id("gm-"),
        "threadId": entry.get("thread_id") or thread_id,
        "snippet": entry["body"][:120],
        "payload": {"headers": headers, "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(entry["body"].encode()).decode().rstrip("=")}},
    }


def _graph_message_model(entry: dict[str, Any], state: _State) -> dict[str, Any]:
    headers = [{"name": "Message-ID", "value": entry["message_id"]}]
    if entry.get("in_reply_to"):
        headers.append({"name": "In-Reply-To", "value": entry["in_reply_to"]})
    return {
        "id": entry.get("provider_id") or state.next_id("gr-"),
        "conversationId": entry.get("conversation_id") or state.next_id("conv-"),
        "subject": entry["subject"],
        "bodyPreview": entry["body"][:120],
        "body": {"contentType": "text", "content": entry["body"]},
        "from": entry.get("from") or {"emailAddress": {"address": "unknown@example.test"}},
        "replyTo": entry.get("reply_to") or [],
        "receivedDateTime": entry["date"],
        "isRead": bool(entry.get("is_read", False)),
        "internetMessageHeaders": headers,
    }


# -- Graph message collections: the documented query forms this fixture implements ---------------------
#
# $search on messages (learn.microsoft.com/en-us/graph/search-query-parameter): the whole value in double
# quotes; every space-separated clause must hold; `from:`, `subject:` and `body:` restrict a clause to that
# documented property and a bare word searches the three default properties (from, subject, body);
# `received` compares dates with the operators of the KQL reference that page links (received>=2021-01-01).
# Words match whole words, case-insensitively (KQL has prefix searches, never substring matches). Results
# come back newest first (documented: sorted by the date the message was sent; the fixture's messages carry
# only receivedDateTime).
# $filter: conversationId eq '<id>', isRead eq true|false and receivedDateTime ge|gt|le|lt <UTC datetime>,
# joined by " and ", plus the parent lookup's internetMessageHeaders/any(...) form.
# $search with $filter or $orderby is refused: "$search and $filter parameters cannot be used together while
# querying message collections" (Microsoft Q&A 1401458, answered by Microsoft staff). Every other form is a
# 400 naming what this fixture does not implement -- never an unfiltered listing.

_WORD = re.compile(r"[^\W_]+")
_RECEIVED = re.compile(r"received(>=|<=|>|<|:|=)(\d{4}-\d{2}-\d{2})")
_FILTER_TERM = re.compile(
    r"conversationId eq '(?P<conversation>[^']*)'"
    r"|isRead eq (?P<read>true|false)"
    r"|receivedDateTime (?P<op>ge|gt|le|lt) (?P<when>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
_COMPARE = {
    ">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
    ":": lambda a, b: a == b, "=": lambda a, b: a == b,
    "ge": lambda a, b: a >= b, "gt": lambda a, b: a > b, "le": lambda a, b: a <= b, "lt": lambda a, b: a < b,
}
_KQL_SPECIAL = set("()<>=*:")


def _words(text: Any) -> set[str]:
    return set(_WORD.findall(str(text or "").casefold()))


def _graph_received(model: dict[str, Any]) -> datetime:
    raw = str(model.get("receivedDateTime") or "")
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _graph_error(text: str) -> dict[str, Any]:
    return {"error": {"code": "BadRequest", "message": f"provider_api_fixture: {text}"}}


def _graph_search(mailbox: list[dict[str, Any]], value: str) -> tuple[list[dict[str, Any]], str]:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return [], f"$search must be the documented double-quoted form, not {value!r}"
    inner = value[1:-1]
    if not inner.split() or '"' in inner or "\\" in inner:
        return [], f"$search {value!r} is empty or holds a phrase or escape this fixture does not implement"
    tests: list[tuple[str, str, Any]] = []
    for clause in inner.split():
        dated = _RECEIVED.fullmatch(clause)
        prop, colon, word = clause.partition(":")
        if dated:
            tests.append(("received", dated.group(1), date.fromisoformat(dated.group(2))))
        elif colon and prop in ("from", "subject", "body") and _words(word) and not set(word) & _KQL_SPECIAL:
            tests.append((prop, "words", _words(word)))
        elif not colon and clause not in ("AND", "OR", "NOT") and _words(clause) and not set(clause) & _KQL_SPECIAL:
            tests.append(("default", "words", _words(clause)))
        else:
            return [], f"$search clause {clause!r} is outside the documented subset this fixture implements"

    def matches(model: dict[str, Any]) -> bool:
        sender = (model.get("from") or {}).get("emailAddress") or {}
        fields = {"from": _words(sender.get("name")) | _words(sender.get("address")),
                  "subject": _words(model.get("subject")),
                  "body": _words((model.get("body") or {}).get("content"))}
        fields["default"] = fields["from"] | fields["subject"] | fields["body"]
        for field, op, operand in tests:
            if field == "received":
                if not _COMPARE[op](_graph_received(model).date(), operand):
                    return False
            elif not operand <= fields[field]:
                return False
        return True

    return sorted((m for m in mailbox if matches(m)), key=_graph_received, reverse=True), ""


def _graph_filter(mailbox: list[dict[str, Any]], value: str) -> tuple[list[dict[str, Any]], str]:
    if value.startswith("internetMessageHeaders/any("):
        needle = value.rsplit("eq '", 1)[-1].rstrip("')").strip()
        found = [m for m in mailbox
                 if any(str(h.get("value") or "") == needle for h in m.get("internetMessageHeaders") or [])]
        return found[:1], ""
    terms = [_FILTER_TERM.fullmatch(term.strip()) for term in value.split(" and ")]
    if not all(terms):
        return [], f"$filter {value!r} is outside the documented subset this fixture implements"

    def matches(model: dict[str, Any]) -> bool:
        for term in terms:
            if term["conversation"] is not None and model.get("conversationId") != term["conversation"]:
                return False
            if term["read"] is not None and bool(model.get("isRead")) != (term["read"] == "true"):
                return False
            if term["op"] is not None:
                when = datetime.fromisoformat(term["when"].replace("Z", "+00:00"))
                if not _COMPARE[term["op"]](_graph_received(model), when):
                    return False
        return True

    return [m for m in mailbox if matches(m)], ""


def _graph_collection(mailbox: list[dict[str, Any]], query: str) -> tuple[int, dict[str, Any]]:
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    search = (params.get("$search") or [None])[0]
    filter_value = (params.get("$filter") or [None])[0]
    orderby = (params.get("$orderby") or [None])[0]
    top = (params.get("$top") or ["10"])[0]
    if search is not None and (filter_value is not None or orderby is not None):
        return 400, _graph_error("$search cannot be combined with $filter or $orderby on messages")
    if not top.isdigit() or not 1 <= int(top) <= 1000:
        return 400, _graph_error(f"$top {top!r} is outside 1..1000")
    if search is not None:
        rows, refusal = _graph_search(mailbox, search)
    elif filter_value is not None:
        rows, refusal = _graph_filter(mailbox, filter_value)
    else:
        rows, refusal = list(mailbox), ""
    if not refusal and orderby is not None:
        if orderby == "receivedDateTime desc":
            rows = sorted(rows, key=_graph_received, reverse=True)
        else:
            refusal = f"$orderby {orderby!r} is outside the subset this fixture implements"
    if refusal:
        return 400, _graph_error(refusal)
    return 200, {"value": rows[: int(top)]}


class ProviderApiServer:
    """Serves /token, /gmail/v1/... and /v1.0/... on one ephemeral loopback port."""

    def __init__(self) -> None:
        self.state = _State()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):  # silence
                pass

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _mailbox_of(self, record):
                # The mailbox a request reaches is the one its bearer token
                # authenticates, never the account slot's metadata.
                return self._principal_for(record)

            def _authenticate(self, method, parsed):
                auth = self.headers.get("Authorization") or ""
                token = auth.removeprefix("Bearer ").strip()
                record = outer.state.access_tokens.get(token)
                self._entry = {"method": method, "path": parsed.path, "query": parsed.query, "token": token,
                               "principal": self._principal_for(record) if record else None,
                               "content_type": str(self.headers.get("Content-Type") or "").split(";")[0]
                               .strip().lower(),
                               "status": None, "identity_withheld": False}
                with outer.state.lock:
                    outer.state.request_log.append(self._entry)
                if not record or record["expires"] < time.time():
                    self._json(401, {"error": "InvalidAuthenticationToken"})
                    return None
                if self._revoked(token, record, method, parsed.path):
                    self._json(401, {"error": "InvalidAuthenticationToken",
                                     "message": "the access token has been revoked"})
                    return None
                return record

            def _revoked(self, token, record, method, path):
                with outer.state.lock:
                    if token in outer.state.revoked_tokens:
                        return True
                    for rule in outer.state.revocation_rules:
                        if (rule["provider"], rule["account"]) != (record.get("prov"), record.get("acct")):
                            continue
                        if rule["method"] and rule["method"] != method:
                            continue
                        if rule["path_suffix"] and not path.endswith(rule["path_suffix"]):
                            continue
                        if rule["every_token"]:
                            return True
                        if rule["armed"]:
                            rule["armed"] = False
                            outer.state.revoked_tokens.add(token)
                            return True
                return False

            def _profile_refusal(self, prov, acct, identityless):
                mode = outer.state.profile_failures.get((prov, acct))
                if not mode:
                    return False
                self._entry["identity_withheld"] = True
                if mode == "forbidden":
                    self._json(403, {"error": {"code": "Forbidden",
                                               "message": "the credential cannot read the profile"}})
                elif mode == "missing":
                    self._json(200, identityless)
                else:  # "drop": the connection closes before any status line
                    self.close_connection = True
                    with contextlib.suppress(OSError):
                        self.connection.close()
                return True

            def _graph_mime_message(self, data, *, require_recipients=True):
                """The documented MIME form: the whole RFC 5322 message, base64-encoded,
                with Content-Type text/plain. Graph builds the message from those headers.
                Invalid base64 and a message without recipients are refused (400)."""
                import binascii
                import email as email_lib
                from email import policy as email_policy
                from email.utils import getaddresses

                compact = "".join(str(data or "").split())
                try:
                    raw = base64.b64decode(compact, validate=True) if compact else b""
                except (binascii.Error, ValueError):
                    raw = b""
                if not raw:
                    self._json(400, {"error": {"code": "ErrorMimeContentInvalidBase64String",
                                               "message": "Invalid base64 string for MIME content."}})
                    return None
                parsed = email_lib.message_from_bytes(raw, policy=email_policy.default)
                headers = {name: str(parsed.get(name) or "") for name in
                           ("From", "To", "Cc", "Subject", "Message-ID", "In-Reply-To", "References")}
                to_only = [addr for _name, addr in getaddresses([headers["To"]]) if addr]
                # Only present fields: strict getaddresses (Python 3.12+) counts one address
                # per field value, so an empty Cc/Bcc value makes it discard the whole list.
                present = [value for value in (headers["To"], headers["Cc"], str(parsed.get("Bcc") or ""))
                           if value.strip()]
                every = [addr for _name, addr in getaddresses(present) if addr]
                if require_recipients and not every:
                    self._json(400, {"error": {"code": "ErrorInvalidRecipients",
                                               "message": "At least one recipient is required."}})
                    return None
                body_part = parsed.get_body(preferencelist=("plain",))
                text = body_part.get_content() if body_part is not None else ""
                senders = [addr for _name, addr in getaddresses([headers["From"]]) if addr]
                return {
                    "representation": "mime",
                    "raw_mime": raw,
                    "mime_headers": headers,
                    "message": {
                        "subject": headers["Subject"],
                        "body": {"contentType": "text", "content": text},
                        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_only],
                        "from": {"emailAddress": {"address": senders[0] if senders else ""}},
                    },
                    "subject": headers["Subject"],
                    "to": to_only,
                    "in_reply_to": headers["In-Reply-To"],
                    "references": headers["References"],
                    "at": time.time(),
                    "_reserved_headers": {},  # Graph assigns its own internetMessageId
                }

            def send_response(self, code, message=None):
                entry = getattr(self, "_entry", None)
                if entry is not None:
                    entry["status"] = code
                super().send_response(code, message)

            def _principal_for(self, record):
                # A token authenticates the principal it was MINTED for; a record
                # without one falls back to the per-grant map.
                if record.get("principal"):
                    return str(record["principal"])
                key = (record.get("prov"), record.get("acct"))
                override = outer.state.principal_overrides.get((key, record.get("grant")))
                if override:
                    return override
                for (prov, acct), rec in outer.state.oauth.items():
                    if acct == record.get("acct") and prov == record.get("prov"):
                        return str((rec.get("rotated_refresh") and rec.get("account_email"))
                                   or rec.get("account_email") or acct)
                return str(record.get("acct") or "")

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length).decode("utf-8") if length else ""
                if parsed.path.endswith("/token"):
                    form = dict(urllib.parse.parse_qsl(data))
                    record = outer.state.oauth.get(("gmail", form.get("account", ""))) or \
                        outer.state.oauth.get(("graph", form.get("account", "")))
                    if record is None:
                        # find by refresh_token value: the current grant or its rotation
                        presented = form.get("refresh_token") or ""
                        for (prov, acct), rec in outer.state.oauth.items():
                            if presented and presented in {rec["refresh_token"], rec.get("rotated_refresh")}:
                                record = rec
                                record["_prov"] = prov
                                record["_acct"] = acct
                                break
                    if record is None:
                        self._json(400, {"error": "invalid_grant",
                                         "error_description": "the refresh token was revoked or is invalid"})
                        return
                    accepted_refresh = form.get("refresh_token")
                    valid_refreshs = {record.get("refresh_token")}
                    if record.get("rotated_refresh"):
                        valid_refreshs.add(record["rotated_refresh"])
                    key = (record.get("_prov"), record.get("_acct"))
                    limit = outer.state.refresh_mint_limit.get(key)
                    exhausted = limit is not None and outer.state.total_mints.get(key, 0) >= limit
                    if accepted_refresh not in valid_refreshs or record.get("revoked") or exhausted:
                        with outer.state.lock:
                            outer.state.token_log.append({"account": key, "grant": accepted_refresh,
                                                          "outcome": "refused"})
                        self._json(400, {"error": "invalid_grant",
                                         "error_description": "the refresh token was revoked or is invalid"})
                        return
                    response_extra = {}
                    if record.get("rotated_refresh"):
                        response_extra["refresh_token"] = record["rotated_refresh"]
                    # Unique per mint (a provider never issues one access token for two
                    # refreshes); each token authenticates the principal it was minted for.
                    token = outer.state.next_id(f"at-{record.get('_prov', 'x')}-")
                    principal = outer.state.mint_principal(key, accepted_refresh, record)
                    with outer.state.lock:
                        outer.state.total_mints[key] = outer.state.total_mints.get(key, 0) + 1
                        outer.state.token_log.append({"account": key, "grant": accepted_refresh,
                                                      "outcome": "minted", "token": token,
                                                      "principal": principal})
                    outer.state.access_tokens[token] = {"prov": record.get("_prov"),
                                                        "acct": record.get("_acct"),
                                                        "grant": accepted_refresh,
                                                        "principal": principal,
                                                        "expires": time.time() + (1 if record.get("expire_access") else 3600)}
                    self._json(200, {"access_token": token, "expires_in": 1 if record.get("expire_access") else 3600,
                                     "token_type": "Bearer", **response_extra})
                    return
                record = self._authenticate("POST", parsed)
                if record is None:
                    return
                prov = record["prov"]
                if prov == "gmail" and parsed.path.endswith("/messages/send"):
                    if outer.state.reject_next_send:
                        outer.state.reject_next_send = False
                        self._json(500, {"error": "synthetic pre-acceptance failure"})
                        return
                    payload = json.loads(data)
                    address = self._mailbox_of(record)
                    outer.state.gmail_sent.setdefault(address, []).append(payload)
                    # Real Gmail makes the sent copy searchable by rfc822msgid; so does the
                    # fixture, so the reconciliation lookup exercises the real path.
                    outer.state._index_gmail_sent(address, payload)
                    if outer.state.drop_after_accept:
                        outer.state.drop_after_accept = False
                        self.close_connection = True
                        with contextlib.suppress(OSError):
                            self.connection.close()
                        return
                    mid = outer.state.next_id("gm-sent-")
                    self._json(200, {"id": mid, "threadId": payload.get("threadId") or outer.state.next_id("th-")})
                    return
                if prov == "graph" and parsed.path.endswith("/sendMail"):
                    if outer.state.reject_next_send:
                        outer.state.reject_next_send = False
                        self._json(500, {"error": {"code": "synthetic", "message": "pre-acceptance failure"}})
                        return
                    content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if content_type == "text/plain":
                        record_payload = self._graph_mime_message(data)
                        if record_payload is None:
                            return
                    elif content_type == "application/json":
                        try:
                            payload = json.loads(data)
                        except ValueError:
                            self._json(400, {"error": {"code": "RequestBodyRead", "message": "invalid JSON"}})
                            return
                        # STRICT provider boundary (documented contract): internetMessageHeaders
                        # on send are for CUSTOM x- headers only; standard RFC headers are a 400.
                        bad_headers = [h.get("name") for h in (payload.get("message") or {}).get("internetMessageHeaders") or []
                                       if not str(h.get("name") or "").lower().startswith("x-")]
                        if bad_headers:
                            self._json(400, {"error": {"code": "InvalidInternetMessageHeader",
                                                       "message": str(bad_headers)}})
                            return
                        record_payload = {
                            "representation": "json",
                            "message": payload.get("message") or {},
                            "subject": (payload.get("message") or {}).get("subject"),
                            "to": [r.get("emailAddress", {}).get("address", "")
                                   for r in (payload.get("message") or {}).get("toRecipients") or []],
                            "at": time.time(),
                            "_reserved_headers": {},  # Graph assigns its own ids; none carried
                        }
                    else:
                        self._json(415, {"error": {"code": "UnsupportedMediaType",
                                                   "message": f"unsupported Content-Type {content_type!r}"}})
                        return
                    outer.state.graph_sent.setdefault(self._mailbox_of(record), []).append(record_payload)
                    if outer.state.drop_after_accept:
                        outer.state.drop_after_accept = False
                        self.close_connection = True
                        with contextlib.suppress(OSError):
                            self.connection.close()
                        return
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if prov == "graph" and "/reply" in parsed.path:
                    # Native reply operation, with its DOCUMENTED addressing: Graph threads it
                    # server-side and addresses it itself. JSON: the parent's replyTo (else its
                    # sender) is the recipient and message.toRecipients are added to it; a
                    # comment together with message.body is a 400. MIME: the parent's sender.
                    parent_id = parsed.path.split("/messages/", 1)[-1].rsplit("/reply", 1)[0]
                    mailbox = outer.state.graph_mailboxes.get(self._mailbox_of(record)) or []
                    parent = next((m for m in mailbox if m.get("id") == parent_id), None)
                    if parent is None:
                        self._json(404, {"error": {"code": "ErrorItemNotFound", "message": "parent not found"}})
                        return
                    sender = [str(((parent.get("from") or {}).get("emailAddress") or {}).get("address") or "")]
                    defaults = [str(((r or {}).get("emailAddress") or {}).get("address") or "")
                                for r in parent.get("replyTo") or []] or sender
                    content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if content_type == "text/plain":
                        if self._graph_mime_message(data, require_recipients=False) is None:
                            return
                        recipients, representation, comment = sender, "mime", ""
                    else:
                        payload = json.loads(data) if data else {}
                        message = payload.get("message") or {}
                        if payload.get("comment") is not None and (message.get("body") or {}).get("content") is not None:
                            self._json(400, {"error": {"code": "ErrorInvalidRequest",
                                                       "message": "Specify either a comment or the body "
                                                                  "property of the message parameter."}})
                            return
                        added = [str(((r or {}).get("emailAddress") or {}).get("address") or "")
                                 for r in message.get("toRecipients") or []]
                        recipients = list(dict.fromkeys(a for a in defaults + added if a))
                        representation, comment = "json", str(payload.get("comment") or "")
                    outer.state.graph_replies.setdefault(self._mailbox_of(record), []).append(
                        {"parent_provider_id": parent_id, "comment": comment, "recipients": recipients,
                         "representation": representation, "at": time.time()})
                    if outer.state.drop_after_accept:
                        outer.state.drop_after_accept = False
                        self.close_connection = True
                        with contextlib.suppress(OSError):
                            self.connection.close()
                        return
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._json(404, {"error": "not found"})

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                record = self._authenticate("GET", parsed)
                if record is None:
                    return
                prov, acct = record["prov"], record["acct"]
                if prov == "gmail" and parsed.path == "/gmail/v1/users/me/profile":
                    if self._profile_refusal(prov, acct, {"messagesTotal": 1}):
                        return
                    self._json(200, {"emailAddress": self._principal_for(record),
                                     "messagesTotal": 1, "threadsTotal": 1, "historyId": "1"})
                    return
                if prov == "graph" and parsed.path == "/v1.0/me":
                    if self._profile_refusal(prov, acct, {"mail": None, "userPrincipalName": None, "id": "u-0"}):
                        return
                    address = self._principal_for(record)
                    mail = None if (prov, acct) in outer.state.graph_mail_null else address
                    self._json(200, {"mail": mail, "userPrincipalName": address, "id": address})
                    return
                if prov == "gmail" and parsed.path.startswith("/gmail/v1/users/me/messages"):
                    mailbox = outer.state.gmail_mailboxes.get(self._mailbox_of(record)) \
                        or outer.state.gmail_mailboxes.get(acct) or []
                    tail = parsed.path[len("/gmail/v1/users/me/messages"):]
                    if tail.startswith("/") and tail.count("/") == 1:
                        mid = tail.strip("/")
                        model = next((m for m in mailbox if m["id"] == mid), None)
                        if model is None:
                            self._json(404, {"error": "not found"})
                            return
                        query = urllib.parse.parse_qs(parsed.query)
                        if "full" in (query.get("format") or ["metadata"]):
                            self._json(200, model)
                            return
                        slim = {k: model[k] for k in ("id", "threadId", "snippet")}
                        slim["payload"] = {"headers": model["payload"]["headers"]}
                        self._json(200, slim)
                        return
                    query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                    matched = []
                    for m in mailbox:
                        blob = json.dumps(m)
                        headers = {str(h.get("name") or "").lower(): str(h.get("value") or "")
                                   for h in m.get("payload", {}).get("headers", [])}
                        ok = True
                        for term in query.split():
                            if term.startswith("from:"):
                                ok = ok and term[5:].strip('"').lower() in blob.lower()
                            elif term.startswith("subject:"):
                                ok = ok and term[8:].strip('"').lower() in blob.lower()
                            elif term.startswith("rfc822msgid:"):
                                ok = ok and term[len("rfc822msgid:"):].strip('"') == headers.get("message-id", "").strip("<>")
                            elif term in ("is:unread", "in:anywhere"):
                                continue
                        if ok:
                            matched.append({"id": m["id"], "threadId": m["threadId"]})
                    self._json(200, {"messages": matched})
                    return
                if prov == "gmail" and parsed.path.startswith("/gmail/v1/users/me/threads/"):
                    tid = parsed.path.rsplit("/", 1)[-1]
                    mailbox = outer.state.gmail_mailboxes.get(self._mailbox_of(record)) \
                        or outer.state.gmail_mailboxes.get(acct) or []
                    msgs = [m for m in mailbox if m["threadId"] == tid]
                    self._json(200, {"id": tid, "messages": msgs})
                    return
                if prov == "graph" and parsed.path.startswith("/v1.0/me/mailFolders/SentItems/messages"):
                    import time as _time
                    sent_items = []
                    for sent in outer.state.graph_sent.get(self._mailbox_of(record)) or []:
                        headers = sent.get("_reserved_headers") or {}
                        sent_items.append({
                            "subject": sent.get("subject"),
                            "toRecipients": [{"emailAddress": {"address": a}} for a in sent.get("to", [])],
                            "receivedDateTime": _time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", _time.gmtime(sent.get("at", _time.time()))),
                            "internetMessageHeaders": (
                                ([{"name": "Message-ID", "value": headers["Message-ID"]}]
                                 if headers.get("Message-ID") else [])
                                + [{"name": name, "value": sent[key]}
                                   for name, key in (("In-Reply-To", "in_reply_to"), ("References", "references"))
                                   if sent.get(key)]),
                        })
                    for reply in outer.state.graph_replies.get(self._mailbox_of(record)) or []:
                        parent = next((m for m in (outer.state.graph_mailboxes.get(self._mailbox_of(record)) or [])
                                       if m.get("id") == reply.get("parent_provider_id")), None)
                        sent_items.append({
                            "subject": ("Re: " + str(parent.get("subject") or "")) if parent else "",
                            "toRecipients": [{"emailAddress": {"address": address}}
                                             for address in reply.get("recipients") or []],
                            "receivedDateTime": _time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                               _time.gmtime(reply.get("at", _time.time()))),
                            "internetMessageHeaders": [],
                        })
                    self._json(200, {"value": sent_items})
                    return
                if prov == "graph" and parsed.path.lower().rstrip("/") == "/v1.0/me/mailfolders/inbox/messages":
                    # The documented well-known Inbox folder; every fixture message is an inbox message.
                    mailbox = outer.state.graph_mailboxes.get(self._mailbox_of(record)) \
                        or outer.state.graph_mailboxes.get(acct) or []
                    self._json(*_graph_collection(mailbox, parsed.query))
                    return
                if prov == "graph" and parsed.path.startswith("/v1.0/me/messages"):
                    mailbox = outer.state.graph_mailboxes.get(self._mailbox_of(record)) \
                        or outer.state.graph_mailboxes.get(acct) or []
                    tail = parsed.path[len("/v1.0/me/messages"):].split("?")[0]
                    if tail.startswith("/") and tail.strip("/"):
                        mid = tail.strip("/")
                        model = next((m for m in mailbox if m["id"] == mid), None)
                        if model is None:
                            self._json(404, {"error": "not found"})
                            return
                        self._json(200, model)
                        return
                    self._json(*_graph_collection(mailbox, parsed.query))
                    return
                self._json(404, {"error": "not found"})

        self._handler = Handler

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def drop_after_accept(self) -> bool:
        return self.state.drop_after_accept

    @drop_after_accept.setter
    def drop_after_accept(self, value: bool) -> None:
        self.state.drop_after_accept = bool(value)

    @property
    def port(self) -> int:
        return self._server.server_port if self._server else 0

    @property
    def token_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/token"

    @property
    def gmail_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def graph_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- fixtures ------------------------------------------------------------
    def add_oauth_client(self, provider: str, account: str, address: str, *,
                         revoke_refresh: bool = False, expire_access: bool = False) -> str:
        refresh = f"rt-{provider}-{account}"
        self.state.oauth[(provider, account)] = {
            "refresh_token": refresh, "account_email": address,
            "revoked": revoke_refresh, "expire_access": expire_access,
        }
        return refresh

    def sent_count(self, provider: str, address: str) -> int:
        if provider == "gmail":
            return len(self.gmail_sent(address))
        return len(self.graph_sent(address)) + len(self.graph_replies_sent(address))

    def serve_principal_for(self, provider: str, account: str, refresh: str, principal: str) -> None:
        self.state.principal_overrides[((provider, account), refresh)] = principal

    def schedule_principals(self, provider: str, account: str, principals: list[str]) -> None:
        """Successive token mints for this account authenticate successive
        principals (the last repeats): a grant switched between two refreshes."""
        with self.state.lock:
            self.state.principal_schedule[(provider, account)] = list(principals)
            self.state.mint_counts[(provider, account)] = 0

    def fail_profile(self, provider: str, account: str, mode: str | None) -> None:
        """Profile reads for this account: 'forbidden' (403), 'missing' (no identity
        fields), 'drop' (connection closed before a status) or None (readable)."""
        if mode:
            self.state.profile_failures[(provider, account)] = mode
        else:
            self.state.profile_failures.pop((provider, account), None)

    def graph_profile_without_mail(self, account: str) -> None:
        """/v1.0/me answers mail=null; only userPrincipalName names the user."""
        self.state.graph_mail_null.add(("graph", account))

    def revoke_token_at_next(self, provider: str, account: str, *, method: str = "",
                             path_suffix: str = "") -> None:
        """The next matching request finds its token revoked server-side: that request and
        every later use of the same token get 401, while a freshly minted token works."""
        with self.state.lock:
            self.state.revocation_rules.append({"provider": provider, "account": account, "method": method,
                                                "path_suffix": path_suffix, "every_token": False,
                                                "armed": True})

    def refuse_every_token(self, provider: str, account: str, *, method: str = "",
                           path_suffix: str = "") -> None:
        """Every matching request gets 401 whatever token it carries: the account's access
        is revoked although the refresh grant still mints tokens."""
        with self.state.lock:
            self.state.revocation_rules.append({"provider": provider, "account": account, "method": method,
                                                "path_suffix": path_suffix, "every_token": True,
                                                "armed": False})

    def refuse_refresh_after(self, provider: str, account: str, mints: int) -> None:
        """After `mints` successful token mints the refresh grant is refused (invalid_grant)."""
        self.state.refresh_mint_limit[(provider, account)] = int(mints)

    def mints(self, provider: str, account: str) -> list[dict[str, Any]]:
        return [e for e in self.state.token_log
                if e["account"] == (provider, account) and e["outcome"] == "minted"]

    def set_refresh(self, provider: str, account: str, refresh: str) -> None:
        record = self.state.oauth.get((provider, account))
        if record:
            record["refresh_token"] = refresh

    def rotate_refresh_for(self, provider: str, account: str, old_refresh: str, new_refresh: str) -> None:
        record = self.state.oauth.get((provider, account))
        if record and record.get("refresh_token") == old_refresh:
            record["rotated_refresh"] = new_refresh

    @property
    def reject_next_send(self) -> bool:
        return self.state.reject_next_send

    @reject_next_send.setter
    def reject_next_send(self, value: bool) -> None:
        self.state.reject_next_send = bool(value)

    def refresh_count(self, provider: str, account: str) -> int:
        return sum(1 for v in self.state.access_tokens.values()
                   if v.get("prov") == provider and v.get("acct") == account
                   and v["expires"] < time.time() + 3600)

    def gmail_inbox(self, address: str, entries: list[dict[str, Any]]) -> None:
        thread = self.state.next_id("th-")
        models = []
        for e in entries:
            model = _gmail_message_model(e, self.state, thread)
            models.append(model)
        self.state.gmail_mailboxes[address] = models

    def gmail_sent(self, address: str) -> list[dict[str, Any]]:
        return list(self.state.gmail_sent.get(address) or [])

    def graph_inbox(self, address: str, entries: list[dict[str, Any]]) -> None:
        models = [_graph_message_model(e, self.state) for e in entries]
        self.state.graph_mailboxes[address] = models

    def graph_sent(self, address: str) -> list[dict[str, Any]]:
        return list(self.state.graph_sent.get(address) or [])

    def graph_replies_sent(self, address: str) -> list[dict[str, Any]]:
        return list(self.state.graph_replies.get(address) or [])

"""Outlook.com / Microsoft365 adapter: Microsoft Graph mail over OAuth2 (KAS transport).

Implements the documented Graph mail shapes
(https://learn.microsoft.com/en-us/graph/outlook-mail-concept-overview):
  * search:  GET /v1.0/me/messages?$search="<KQL>" for sender/subject words (with
             any date window), ?$filter= for read state, or the Inbox folder's
             collection when there is no filter → summaries
  * open:    GET /v1.0/me/messages/{id} (full body) and conversation threads via
             GET /v1.0/me/conversations/{id}/threads or the message's
             conversationId grouping
  * send:    POST /v1.0/me/sendMail: a reply as the exact base64 MIME message
             (its parent binding in In-Reply-To/References), a new message as
             the exact JSON message
  * identity: GET /v1.0/me?$select=mail,userPrincipalName (User.Read), read with
             the exact token a bound request will carry

No policy here: permissions/approvals/receipts stay in the VOOL seam.
`api_base` and `token_url` are injectable for local recorded-shape contract
tests; live tenant access is a labelled gate.
"""
from __future__ import annotations

import base64
import email as email_lib
import json
import re
from typing import Any

from core.email_providers.base import (
    BINDING_REFUSALS,
    PrincipalBinding,
    ProviderAccountError,
    ProviderCapability,
    _load_oauth_handle,
    _OAuthClient,
)
from core.email_tools import EmailReadResult, EmailResult, MailSearch

GRAPH_API_BASE = "https://graph.microsoft.com"
GRAPH_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
# User.Read is the least-privileged delegated permission for GET /me, the profile
# read that proves which mailbox a credential authenticates before it sends.
GRAPH_SCOPES = "offline_access User.Read Mail.ReadWrite Mail.Send"
_MAX_RESULT = 50

_MESSAGE_SUMMARY_FIELDS = ("id,conversationId,subject,from,receivedDateTime,bodyPreview,"
                           "internetMessageHeaders,threadTopic")


def _kql_words(value: str) -> list[str]:
    """A typed filter value as KQL words: lower-cased, so no word can read as an AND/OR/NOT operator;
    split at every character that is not part of a word or an address (a tokenized search separates
    words there anyway); a leading or trailing +, -, . or ' (KQL inclusion/exclusion marks) dropped."""
    words = (word.strip("+-.'") for word in re.findall(r"[\w@.+'-]+", str(value or "").casefold()))
    return [word for word in words if re.search(r"[^\W_]", word)]


def _mailbox_text(address: dict[str, Any]) -> str:
    """A Graph emailAddress as the mailbox text a Gmail From header carries -- "Name <address>", the
    name quoted where RFC 5322 requires it -- so both providers give the model the same sender
    evidence. The bare address when Graph gives no name, or gives the address itself as the name."""
    from email.headerregistry import Address

    addr_spec = str(address.get("address") or "").strip()
    name = str(address.get("name") or "").strip()
    if not name or not addr_spec or name.casefold() == addr_spec.casefold():
        return addr_spec
    try:
        return str(Address(display_name=name, addr_spec=addr_spec))
    except Exception:  # an address the header registry cannot read keeps only the address
        return addr_spec


class GraphAdapter:
    provider = "graph"
    capabilities = ProviderCapability(search=True, open_message=True, open_thread=True, send_threaded=True)

    def __init__(self, *, account: str, creds: dict[str, Any], api_base: str = GRAPH_API_BASE,
                 token_url: str = GRAPH_TOKEN_URL):
        self.account = str(account or "default")
        self._api_base = api_base.rstrip("/")
        handle = creds.get("_oauth_handle")
        if not isinstance(handle, dict) or not handle.get("refresh_token"):
            handle = _load_oauth_handle("graph", self.account)
        if not handle.get("refresh_token"):
            raise ProviderAccountError(
                "needs_setup",
                f"Microsoft account '{self.account}' has no Graph connection yet. Connect it through email "
                "account setup (Microsoft sign-in consent); I will never ask for your password in chat.",
                provider="graph",
            )
        handle = dict(handle)
        self._oauth = _OAuthClient(
            provider="graph", token_url=str(handle.get("token_url") or token_url),
            client_id=str(handle.get("client_id") or ""), client_secret=str(handle.get("client_secret") or ""),
            refresh_token=str(handle.get("refresh_token") or ""), scopes=GRAPH_SCOPES,
            on_rotation=self._persist_rotated_refresh,
        )
        # The outward sending address, composed exactly like the local account
        # binding (core.email_tools.configured_identity) so the two compare.
        self._alias = str(creds.get("from_addr") or creds.get("username") or "")

    def principal_binding(self, bound_identity: str) -> PrincipalBinding:
        return PrincipalBinding(provider="graph", bound_identity=bound_identity, alias=self._alias,
                                read_principals=self._profile_principals)

    def _profile_principals(self, token: str) -> list[str]:
        """The signed-in user's own identities, read WITH `token`: `mail` (null for
        some accounts) and `userPrincipalName` both name the user that exact
        credential authenticates."""
        profile = self._oauth.read_with_token(
            "GET", f"{self._api_base}/v1.0/me?$select=mail,userPrincipalName", token)
        return [str(profile.get("mail") or ""), str(profile.get("userPrincipalName") or "")]

    def _persist_rotated_refresh(self, new_refresh: str) -> None:
        """Microsoft may rotate the refresh token on use; persist it or the next
        restart loses the grant. The handle stays in the credential store."""
        from core import credential_store

        handle = _load_oauth_handle("graph", self.account)
        if not handle:
            return
        handle["refresh_token"] = new_refresh
        credential_store.store_credential(
            f"email.oauth.graph.{self.account}", json.dumps(handle), label="graph oauth (rotated)",
        )

    # -- shared surface -----------------------------------------------------
    def search(self, request: MailSearch, *, limit: int) -> EmailReadResult:
        """The typed filters in Graph's documented message query forms
        (https://learn.microsoft.com/en-us/graph/search-query-parameter):
          * sender/subject words, with any date window: ONE $search whose whole value is double-quoted
            KQL -- from:<word> and subject:<word> clauses, all of which must hold, and received>=<since>
            / received<<before> (comparison operators of the KQL reference that page links);
          * read state, with any date window: a $filter on isRead and receivedDateTime, and no $search;
          * no filter: the Inbox folder's own collection, newest first.
        $search cannot carry read state and cannot be combined with $filter on messages (Microsoft Q&A
        1401458), so words together with a read state are refused before any request."""
        from urllib.parse import quote

        words = [f"from:{word}" for word in _kql_words(request.sender)]
        words += [f"subject:{word}" for word in _kql_words(request.subject)]
        read_state = "false" if request.unseen_only else ("true" if request.seen_only else "")
        if (request.sender.strip() or request.subject.strip()) and not words:
            return EmailReadResult(False, "invalid_arguments",
                                   "The sender or subject holds no word Microsoft Graph can search for, so "
                                   "nothing was searched.")
        if words and read_state:
            return EmailReadResult(False, "unsupported_filter",
                                   "Microsoft Graph cannot search sender or subject words and filter by read "
                                   "state in one request ($search and $filter cannot be combined on "
                                   "messages), so nothing was searched. Ask for the words, or for unread or "
                                   "read messages, on their own.")
        fields = f"$select={_MESSAGE_SUMMARY_FIELDS}&$top={min(max(int(limit or 1), 1), _MAX_RESULT)}"
        if words:
            clauses = [*words, *([f"received>={request.since}"] if request.since else []),
                       *([f"received<{request.before}"] if request.before else [])]
            search_value = '"' + " ".join(clauses) + '"'
            url = f"{self._api_base}/v1.0/me/messages?{fields}&$search={quote(search_value, safe='')}"
        else:
            predicates = [*([f"receivedDateTime ge {request.since}T00:00:00Z"] if request.since else []),
                          *([f"receivedDateTime lt {request.before}T00:00:00Z"] if request.before else []),
                          *([f"isRead eq {read_state}"] if read_state else [])]
            if predicates:
                filter_value = " and ".join(predicates)
                url = f"{self._api_base}/v1.0/me/messages?{fields}&$filter={quote(filter_value, safe='')}"
            else:
                url = (f"{self._api_base}/v1.0/me/mailFolders/inbox/messages?{fields}"
                       f"&$orderby={quote('receivedDateTime desc', safe='')}")
        try:
            listing = self._oauth.api_read("GET", url)
        except ProviderAccountError as exc:
            return EmailReadResult(False, exc.kind, str(exc))
        messages = [self._summary(m) for m in listing.get("value") or []]
        return EmailReadResult(True, "executed",
                               f"{len(messages)} message(s) matched (Graph).", messages=messages)

    @staticmethod
    def _summary(m: dict[str, Any]) -> dict[str, str]:
        headers = {str(h.get("name") or "").lower(): str(h.get("value") or "")
                   for h in m.get("internetMessageHeaders") or []}
        sender = m.get("from") or {}
        return {
            "message_id": str(headers.get("message-id") or m.get("id") or ""),
            "from": _mailbox_text(sender.get("emailAddress") or {}),
            "to": str(headers.get("to") or ""),
            "subject": str(m.get("subject") or ""),
            "date": str(m.get("receivedDateTime") or ""),
            "snippet": str(m.get("bodyPreview") or ""),
            "provider_id": str(m.get("id") or ""),
            "conversation_id": str(m.get("conversationId") or ""),
        }

    def open_message(self, *, provider_id: str) -> EmailReadResult:
        try:
            m = self._oauth.api_read(
                "GET", f"{self._api_base}/v1.0/me/messages/{provider_id}?$select="
                       "id,conversationId,subject,from,toRecipients,receivedDateTime,body,"
                       "internetMessageHeaders,threadTopic")
        except ProviderAccountError as exc:
            if exc.kind == "provider_refused" and exc.status == 404:
                return EmailReadResult(False, "not_found", f"Graph message {provider_id} not found.")
            return EmailReadResult(False, exc.kind, str(exc))
        entry = self._full_entry(m)
        return EmailReadResult(True, "executed", f"Opened Graph message {provider_id}.", messages=[entry])

    def open_thread(self, *, thread_id: str) -> EmailReadResult:
        from urllib.parse import quote

        filter_value = quote("conversationId eq '" + str(thread_id) + "'", safe="='")
        try:
            listing = self._oauth.api_read(
                "GET", f"{self._api_base}/v1.0/me/messages?$select={_MESSAGE_SUMMARY_FIELDS}"
                       f"&$filter={filter_value}&$top={_MAX_RESULT}")
        except ProviderAccountError as exc:
            return EmailReadResult(False, exc.kind, str(exc))
        entries = [self._summary(m) for m in listing.get("value") or []]
        return EmailReadResult(True, "executed",
                               f"Opened Graph conversation of {len(entries)} message(s).", messages=entries)

    def _full_entry(self, m: dict[str, Any]) -> dict[str, str]:
        entry = self._summary(m)
        body = m.get("body") or {}
        text = str(body.get("content") or "")
        if str(body.get("contentType") or "").lower() == "html":
            import re

            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        entry["body"] = text
        return entry

    @staticmethod
    def _recipient_objects(to_value: str) -> list[dict[str, Any]]:
        """The approved recipient list as Graph toRecipients objects — one entry
        per approved address, exactly as reviewed (never the parent's defaults)."""
        recipients: list[dict[str, Any]] = []
        for piece in to_value.split(","):
            piece = piece.strip()
            if piece:
                recipients.append({"emailAddress": {"address": piece.strip("<>").split("<")[-1].strip(">")}})
        return recipients

    @staticmethod
    def _decoded_subject(parsed: Any) -> str:
        """The subject as APPROVED: RFC 2047 encoded-words unfolded and decoded
        to their Unicode text (`str(header)` alone ships the literal =?utf-8?...?
        wire form, which is not what the user reviewed)."""
        from email.header import decode_header, make_header

        raw = parsed.get("Subject")
        if not raw:
            return ""
        try:
            return str(make_header(decode_header(str(raw))))
        except Exception:
            return str(raw)

    @staticmethod
    def _decoded_text(raw_mime: bytes) -> tuple[str, str]:
        """(text, subject) from the MIME exactly as approved — transfer-decoded.

        get_payload() WITHOUT decode returns the still-encoded wire form
        (quoted-printable wrapping, base64); the JSON body must carry the decoded
        approved text, preserving Unicode and paragraph structure."""
        parsed = email_lib.message_from_bytes(raw_mime)

        def _decode(part) -> str:
            raw = part.get_payload(decode=True) or b""
            return raw.decode(part.get_content_charset() or "utf-8", "replace")

        text = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    text = _decode(part)
                    break
        else:
            text = _decode(parsed)
        return text, GraphAdapter._decoded_subject(parsed)

    def _resolve_parent_message(self, reply_to_rfc: str, *,
                                binding: PrincipalBinding | None = None) -> str:
        """The Graph provider id of the reviewed parent message, via a $filter over
        internetMessageHeaders (readable on GET even though only x- headers are
        settable on send). Microsoft's documentation does not establish that a live
        tenant honours this filter: a live-provider gate. Only the receipt's
        parent_provider_id depends on it -- the reply's parent binding travels in its
        MIME -- and an empty answer never blocks the send. The lookup reads the BOUND
        mailbox: an identity refusal surfaces, it is never mistaken for a missing
        parent."""
        from urllib.parse import quote

        needle = str(reply_to_rfc or "").strip()
        if not needle:
            return ""
        filter_value = quote(
            "internetMessageHeaders/any(h:h/name eq 'Message-ID' and h/value eq '" + needle + "')",
            safe="='()/",
        )
        try:
            listing = self._oauth.api_read(
                "GET", f"{self._api_base}/v1.0/me/messages?$select=id,subject&$filter={filter_value}&$top=5",
                binding=binding)
        except ProviderAccountError as exc:
            if exc.kind in BINDING_REFUSALS:
                raise
            return ""
        for m in listing.get("value") or []:
            return str(m.get("id") or "")
        return ""

    def send(self, *, raw_mime: bytes, thread_id: str = "", reply_to_rfc: str = "",
             expected_identity: str = "") -> EmailResult:
        parsed = email_lib.message_from_bytes(raw_mime)
        parent = str(reply_to_rfc or "").strip()
        if parent:
            carried = str(parsed.get("In-Reply-To") or "").strip()
            if carried != parent or parent not in str(parsed.get("References") or "").split():
                # STRUCTURAL GUARD, before any request: an approved reply must leave
                # carrying its approved parent binding. Without it, or with another
                # parent, it would silently become a different message.
                return EmailResult(
                    False, "invalid_message",
                    f"The reply does not carry its approved parent binding (In-Reply-To "
                    f"{carried or 'missing'}, expected {parent}); nothing was sent.",
                )
        is_reply = bool(parent or str(parsed.get("In-Reply-To") or "").strip())
        # AUTHENTICATED DISPATCH: the parent lookup and the POST each carry a
        # token the provider's own profile confirmed as the bound mailbox
        # (base.PrincipalBinding). Local metadata authorizes nothing here.
        binding = self.principal_binding(expected_identity)
        text, subject = self._decoded_text(raw_mime)
        recipients = self._recipient_objects(str(parsed.get("To") or ""))
        reserved_id = str(parsed.get("Message-ID") or "")

        # EXACT REPRESENTATION, chosen by what the approved message is.
        #  * A REPLY leaves as ONE documented sendMail in MIME form: base64 of the exact
        #    canonical RFC 5322 bytes with Content-Type text/plain. Those bytes carry the
        #    approved From, To, Subject, body and reserved Message-ID together with the
        #    In-Reply-To/References parent binding. The native /reply operation is NOT
        #    used for reviewed content: Graph addresses a native reply itself (the
        #    parent's replyTo, else its sender; JSON toRecipients are added to that and a
        #    MIME reply goes to the parent's sender), which would change who receives it.
        #  * A NEW message keeps the exact JSON sendMail body: the decoded subject, the
        #    transfer-decoded text and the approved toRecipients (JSON
        #    internetMessageHeaders are custom-x-only, and there is no parent to bind).
        # Whether Graph keeps our Message-ID on a MIME send is a live-provider question,
        # so reconciliation keeps treating Graph evidence as bounded candidates.
        request: dict[str, Any]
        if is_reply:
            request = {"raw_body": base64.b64encode(raw_mime), "content_type": "text/plain"}
        else:
            request = {"payload": {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "text", "content": text},
                    "toRecipients": recipients,
                },
                "saveToSentItems": True,
            }}
        shape: dict[str, Any] = {"representation": "mime" if is_reply else "json"}
        try:
            parent_provider_id = self._resolve_parent_message(parent, binding=binding) if parent else ""
            outcome = self._oauth.api_mutation(
                "POST", f"{self._api_base}/v1.0/me/sendMail", binding=binding, **request)
        except ProviderAccountError as exc:
            return EmailResult(False, exc.kind, str(exc), details={**binding.evidence(), **shape})
        if parent:
            shape["parent_resolved"] = bool(parent_provider_id)
        if outcome.kind == "uncertain":
            return EmailResult(
                True, "delivery_unknown",
                "The Graph connection dropped while (or after) submitting the message, so "
                "delivery is UNKNOWN: it may have been accepted. Not resent; reconcile via the "
                "provider's sent view.",
                details={**binding.evidence(), **shape},
            )
        if outcome.kind == "unsent":
            return EmailResult(
                False, "send_failed",
                f"The message provably never reached Graph ({outcome.note}); safe to retry after review.",
                details={**binding.evidence(), **shape},
            )
        # accepted: 202 is ACCEPTED FOR PROCESSING by contract (no response body);
        # that is the truthful acceptance level — recipient inbox delivery is NOT
        # claimed, and no later body/typing failure downgrades it.
        note = f" {outcome.note}" if outcome.note else ""
        return EmailResult(
            True, "executed",
            f"Microsoft Graph accepted the message for sending (accepted-for-processing){note}.",
            details={"to": [str(r["emailAddress"]["address"]) for r in recipients],
                     "subject": subject,
                     "message_id": reserved_id,
                     "graph_assigned": True,
                     "parent_provider_id": parent_provider_id,
                     "conversation_id": str(thread_id or ""),
                     "provider": "graph", "acceptance": "accepted_for_processing",
                     **binding.evidence(), **shape},
        )

    def message_in_sent(self, *, message_id: str, subject: str = "", recipient: str = "",
                        recipients: list[str] | None = None, since_epoch: float = 0.0,
                        expected_identity: str = "") -> tuple[bool, str]:
        """Reconciliation in the account's SentItems, with HONEST evidence levels.

        Graph assigns its own Message-IDs (our reserved id is ours, not the
        provider's), so exact confirmation is possible ONLY when a sent item
        carries our Message-ID verbatim. Subject+recipients inside the
        reservation window is CANDIDATE evidence — it cannot uniquely prove this
        send occurred, so it NEVER yields a confirmation on its own; the note
        exposes the candidate count instead. The listing is ONE explicitly
        ordered page ($orderby=receivedDateTime desc, $top=50) and the note
        states that bound. With `expected_identity` the read is bound to that
        verified mailbox; an identity refusal is raised, never reported as
        absence."""
        from datetime import datetime
        from datetime import timezone as _tz
        from urllib.parse import quote

        binding = self.principal_binding(expected_identity) if expected_identity else None
        try:
            listing = self._oauth.api_read(
                "GET", f"{self._api_base}/v1.0/me/mailFolders/SentItems/messages"
                       f"?$select=subject,toRecipients,receivedDateTime,internetMessageHeaders"
                       f"&$orderby={quote('receivedDateTime desc', safe='')}&$top=50",
                binding=binding)
        except ProviderAccountError as exc:
            if exc.kind in BINDING_REFUSALS:
                raise
            return False, f"provider lookup failed ({exc.kind})"
        items = listing.get("value") or []
        approved_recipients = {str(r).strip().lower() for r in (recipients or ([recipient] if recipient else []))}

        def _when(item: dict[str, Any]) -> float:
            raw = str(item.get("receivedDateTime") or "")
            try:
                moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return 0.0
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=_tz.utc)
            return moment.timestamp()

        window = 3600.0
        candidates: list[dict[str, Any]] = []
        for item in items:
            headers = {str(h.get("name") or "").lower(): str(h.get("value") or "")
                       for h in item.get("internetMessageHeaders") or []}
            if headers.get("message-id", "").strip() == message_id:
                return True, "matched exactly by the reserved Message-ID in the sent item"
            if not subject or str(item.get("subject") or "") != subject:
                continue
            item_recipients = {str((r.get("emailAddress") or {}).get("address") or "").strip().lower()
                               for r in item.get("toRecipients") or []}
            if approved_recipients and not approved_recipients <= item_recipients:
                continue
            when = _when(item)
            if since_epoch and when and abs(when - since_epoch) > window:
                continue
            candidates.append(item)
        bound = "newest 50 by receivedDateTime"
        if not candidates:
            return False, f"no candidate in the sent view (search bounded: {bound})"
        return False, (
            f"{len(candidates)} candidate message(s) match subject+recipients in the reservation "
            f"window (search bounded: {bound}); candidate evidence is not unique proof this send "
            "occurred, so delivery is not confirmed"
        )

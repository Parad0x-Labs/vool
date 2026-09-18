"""Gmail / Google Workspace adapter: Gmail REST API over OAuth2 (KAS transport).

Implements the account's provider surface against the documented API shapes
(https://developers.google.com/workspace/gmail/api/guides):
  * search:  GET /gmail/v1/users/me/messages?q=<query>  → ids, then
             GET /gmail/v1/users/me/messages/{id}?format=metadata for summaries
  * open:    GET .../messages/{id}?format=full (body decoded by our shared
             MIME reader) and GET .../threads/{id} for whole threads
  * send:    POST .../messages/send with raw RFC822 (base64url) + threadId so
             the reply lands in the same Gmail thread (In-Reply-To/References
             are already in the MIME we build)
  * identity: GET .../profile (users.getProfile; gmail.modify covers it), read
             with the exact token a bound request will carry

The adapter contains NO policy: permissions, approvals, drafts and receipts stay
in the VOOL seam (core.email_tools / core.email_drafts). `api_base` is injectable
so contract tests run against a local recorded-shape server; the production
default is Google's endpoint and live access remains a labelled gate.
"""
from __future__ import annotations

import base64
import email as email_lib
import json
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

GMAIL_API_BASE = "https://gmail.googleapis.com"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.modify"
_MAX_RESULT_IDS = 50


def _gmail_query(request: MailSearch) -> str:
    """The typed filters as Gmail search operators (https://support.google.com/mail/answer/7190):
    from:, subject:, after:/before: (YYYY/MM/DD), is:unread / is:read, and in:inbox with no filter."""
    terms: list[str] = []

    def _quoted(value: str) -> str:
        value = str(value or "").strip()
        return f'"{value}"' if " " in value else value

    if request.sender.strip():
        terms.append(f"from:{_quoted(request.sender)}")
    if request.subject.strip():
        terms.append(f"subject:{_quoted(request.subject)}")
    if request.since:
        terms.append(f"after:{request.since.replace('-', '/')}")
    if request.before:
        terms.append(f"before:{request.before.replace('-', '/')}")
    if request.unseen_only:
        terms.append("is:unread")
    if request.seen_only:
        terms.append("is:read")
    return " ".join(terms) or "in:inbox"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class GmailAdapter:
    provider = "gmail"
    capabilities = ProviderCapability(search=True, open_message=True, open_thread=True, send_threaded=True)

    def __init__(self, *, account: str, creds: dict[str, Any], api_base: str = GMAIL_API_BASE,
                 token_url: str = GMAIL_TOKEN_URL):
        self.account = str(account or "default")
        self._api_base = api_base.rstrip("/")
        handle = creds.get("_oauth_handle")
        if not isinstance(handle, dict) or not handle.get("refresh_token"):
            handle = _load_oauth_handle("gmail", self.account)
        if not handle.get("refresh_token"):
            raise ProviderAccountError(
                "needs_setup",
                f"Gmail account '{self.account}' has no OAuth connection yet. Connect it through email "
                "account setup (Google sign-in consent); I will never ask for your password in chat.",
                provider="gmail",
            )
        handle = dict(handle)
        self._oauth = _OAuthClient(
            provider="gmail", token_url=str(handle.get("token_url") or token_url),
            client_id=str(handle.get("client_id") or ""), client_secret=str(handle.get("client_secret") or ""),
            refresh_token=str(handle.get("refresh_token") or ""), scopes=GMAIL_SCOPES,
            on_rotation=self._persist_rotated_refresh,
        )
        # The outward sending address, composed exactly like the local account
        # binding (core.email_tools.configured_identity) so the two compare.
        self._alias = str(creds.get("from_addr") or creds.get("username") or "")

    def _persist_rotated_refresh(self, new_refresh: str) -> None:
        """Google may rotate the refresh token on use; persist it or the next restart
        loses the grant. The handle stays in the credential store — nowhere else."""
        from core import credential_store

        handle = _load_oauth_handle("gmail", self.account)
        if not handle:
            return
        handle["refresh_token"] = new_refresh
        credential_store.store_credential(
            f"email.oauth.gmail.{self.account}", json.dumps(handle), label="gmail oauth (rotated)",
        )

    def principal_binding(self, bound_identity: str) -> PrincipalBinding:
        return PrincipalBinding(provider="gmail", bound_identity=bound_identity, alias=self._alias,
                                read_principals=self._profile_principals)

    def _profile_principals(self, token: str) -> list[str]:
        """users.getProfile read WITH `token`: emailAddress is the mailbox that
        exact credential authenticates."""
        profile = self._oauth.read_with_token("GET", f"{self._api_base}/gmail/v1/users/me/profile", token)
        return [str(profile.get("emailAddress") or "")]

    # -- shared surface -----------------------------------------------------
    def search(self, request: MailSearch, *, limit: int) -> EmailReadResult:
        """The typed filters as one Gmail query (_gmail_query)."""
        from urllib.parse import quote

        query = _gmail_query(request)
        try:
            listing = self._oauth.api_read(
                "GET",
                f"{self._api_base}/gmail/v1/users/me/messages"
                f"?maxResults={min(limit, _MAX_RESULT_IDS)}&q={quote(query, safe=':')}")
        except ProviderAccountError as exc:
            return EmailReadResult(False, exc.kind, str(exc))
        messages: list[dict[str, str]] = []
        for item in listing.get("messages") or []:
            entry = self._summary(str(item.get("id") or ""))
            if entry:
                messages.append(entry)
            if len(messages) >= limit:
                break
        return EmailReadResult(True, "executed", f"{len(messages)} message(s) matched (Gmail).", messages=messages)

    def _summary(self, message_id: str) -> dict[str, str] | None:
        data = self._message(message_id, "metadata")
        if data is None:
            return None
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        return {
            "message_id": str(headers.get("message-id") or message_id),
            "from": str(headers.get("from") or ""),
            "to": str(headers.get("to") or ""),
            "subject": str(headers.get("subject") or ""),
            "date": str(headers.get("date") or ""),
            "snippet": str(data.get("snippet") or ""),
            "provider_id": message_id,
            "thread_id": str(data.get("threadId") or ""),
        }

    def _message(self, message_id: str, fmt: str, *,
                 binding: PrincipalBinding | None = None) -> dict[str, Any] | None:
        try:
            return self._oauth.api_read("GET",
                f"{self._api_base}/gmail/v1/users/me/messages/{message_id}?format={fmt}", binding=binding)
        except ProviderAccountError as exc:
            if exc.kind == "provider_refused" and exc.status == 404:
                return None
            raise

    def open_message(self, *, provider_id: str) -> EmailReadResult:
        data = self._message(provider_id, "full")
        if data is None:
            return EmailReadResult(False, "not_found", f"Gmail message {provider_id} not found.")
        entry = self._entry_from_full(data)
        return EmailReadResult(True, "executed", f"Opened Gmail message {provider_id}.", messages=[entry])

    def open_thread(self, *, thread_id: str) -> EmailReadResult:
        try:
            thread = self._oauth.api_read("GET", f"{self._api_base}/gmail/v1/users/me/threads/{thread_id}")
        except ProviderAccountError as exc:
            if exc.kind == "provider_refused" and exc.status == 404:
                return EmailReadResult(False, "not_found", f"Gmail thread {thread_id} not found.")
            return EmailReadResult(False, exc.kind, str(exc))
        entries = []
        for message in thread.get("messages") or []:
            entries.append(self._entry_from_full(message))
        return EmailReadResult(True, "executed",
                               f"Opened Gmail thread of {len(entries)} message(s).", messages=entries)

    def _entry_from_full(self, data: dict[str, Any]) -> dict[str, str]:
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        body_bytes = b""
        payload = data.get("payload") or {}
        if str(payload.get("mimeType") or "").startswith("multipart/"):
            for part in payload.get("parts") or []:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    body_bytes = _b64url_decode(str(part["body"]["data"]))
                    break
        elif payload.get("body", {}).get("data"):
            body_bytes = _b64url_decode(str(payload["body"]["data"]))
        parsed = email_lib.message_from_bytes(b"Content-Type: text/plain\r\n\r\n" + body_bytes)
        return {
            "message_id": str(headers.get("message-id") or data.get("id") or ""),
            "from": str(headers.get("from") or ""),
            "to": str(headers.get("to") or ""),
            "subject": str(headers.get("subject") or ""),
            "date": str(headers.get("date") or ""),
            "snippet": str(data.get("snippet") or ""),
            "body": body_bytes.decode("utf-8", "replace") if body_bytes else str(parsed.get_payload()),
            "in_reply_to": str(headers.get("in-reply-to") or ""),
            "references": str(headers.get("references") or ""),
            "provider_id": str(data.get("id") or ""),
            "thread_id": str(data.get("threadId") or ""),
        }

    def _resolve_thread_for_parent(self, reply_to_rfc: str, *,
                                   binding: PrincipalBinding | None = None) -> str:
        """The provider thread the reviewed reply must join, resolved from the ACTUAL
        parent message on this account (Gmail requires threadId plus compliant
        reply headers and a matching subject to place a message in a thread). A
        thread id echoed by a success RESPONSE is never used as the binding —
        only this lookup is. The lookup reads the BOUND mailbox: an identity
        refusal surfaces, it is never mistaken for a missing parent."""
        from urllib.parse import quote

        needle = str(reply_to_rfc or "").strip().strip("<>")
        if not needle:
            return ""
        query = quote(f"rfc822msgid:{needle}", safe=":")
        try:
            listing = self._oauth.api_read(
                "GET", f"{self._api_base}/gmail/v1/users/me/messages?maxResults=5&q={query}", binding=binding)
        except ProviderAccountError as exc:
            if exc.kind in BINDING_REFUSALS:
                raise
            return ""
        for item in listing.get("messages") or []:
            detail = self._message(str(item.get("id") or ""), "metadata", binding=binding)
            if detail:
                return str(detail.get("threadId") or "")
        return ""

    def send(self, *, raw_mime: bytes, thread_id: str = "", reply_to_rfc: str = "",
             expected_identity: str = "") -> EmailResult:
        # AUTHENTICATED DISPATCH: the parent lookup and the POST each carry a
        # token the provider's own profile confirmed as the bound mailbox
        # (base.PrincipalBinding). Local metadata authorizes nothing here.
        binding = self.principal_binding(expected_identity)
        try:
            # Bind the outgoing request to the parent's provider thread: an explicitly
            # resolved id wins; otherwise resolve from the reviewed parent message.
            resolved_thread = str(thread_id or "") or self._resolve_thread_for_parent(
                reply_to_rfc, binding=binding)
            payload: dict[str, Any] = {"raw": _b64url(raw_mime)}
            if resolved_thread:
                payload["threadId"] = resolved_thread
            outcome = self._oauth.api_mutation(
                "POST", f"{self._api_base}/gmail/v1/users/me/messages/send", payload=payload, binding=binding)
        except ProviderAccountError as exc:
            return EmailResult(False, exc.kind, str(exc), details=binding.evidence())
        if outcome.kind == "uncertain":
            return EmailResult(
                True, "delivery_unknown",
                "The Gmail API connection dropped while (or after) submitting the message, so "
                "delivery is UNKNOWN: it may have been accepted. Not resent; reconcile via the "
                "provider's sent view using the reserved Message-ID.",
                details=binding.evidence(),
            )
        if outcome.kind == "unsent":
            return EmailResult(
                False, "send_failed",
                f"The message provably never reached Gmail ({outcome.note}); safe to retry after review.",
                details=binding.evidence(),
            )
        # accepted: sticky through every later decoding/typing failure.
        body = outcome.payload if isinstance(outcome.payload, dict) else {}
        message_id = str(body.get("id") or "")
        parsed = email_lib.message_from_bytes(raw_mime)
        note = f" {outcome.note}" if outcome.note else ""
        return EmailResult(
            True, "executed",
            f"Gmail accepted the message for sending{note}.",
            details={"to": [str(parsed.get("To") or "")], "subject": str(parsed.get("Subject") or ""),
                     "message_id": str(parsed.get("Message-ID") or ""), "provider_id": message_id,
                     # The RESOLVED thread binding — never the response echo.
                     "thread_id": resolved_thread,
                     "provider_echo_thread_id": str(body.get("threadId") or ""),
                     "provider": "gmail", "acceptance": "provider_accepted",
                     "representation": "mime",
                     **({"parent_resolved": bool(resolved_thread)} if str(reply_to_rfc or "").strip() else {}),
                     **binding.evidence()},
        )

    def message_in_sent(self, *, message_id: str, subject: str = "", recipient: str = "",
                        recipients: list[str] | None = None,
                        since_epoch: float = 0.0, expected_identity: str = "") -> tuple[bool, str]:
        """Gmail's own copy of OUR Message-ID, in any label (the reconciliation
        equivalent of the standards path's Sent folder). Gmail keeps our reserved
        id, so the id match is the evidence; the extra hints are unused here
        (they exist for providers that assign their own message identities).
        With `expected_identity` the read is bound to that verified mailbox, and
        an identity refusal is raised, never reported as absence."""
        from urllib.parse import quote as _quote

        binding = self.principal_binding(expected_identity) if expected_identity else None
        needle = message_id.strip("<>")
        query = _quote(f"in:anywhere rfc822msgid:{needle}", safe=":")
        try:
            listing = self._oauth.api_read(
                "GET",
                f"{self._api_base}/gmail/v1/users/me/messages?maxResults=25&q={query}",
                binding=binding,
            )
        except ProviderAccountError as exc:
            if exc.kind in BINDING_REFUSALS:
                raise
            return False, f"provider lookup failed ({exc.kind})"
        matched = bool(listing.get("messages"))
        return matched, ("matched by Message-ID in the provider's view" if matched
                         else "not found by Message-ID in the provider's view")

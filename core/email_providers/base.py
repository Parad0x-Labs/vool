"""The shared provider contract every email transport implements.

One shape, three authorities kept where they belong:
  * TRANSPORT (this file's implementors): connect/refresh/search/open/send for
    one account. No policy, no permission decisions, no chat.
  * VOOL (core.email_tools / email_drafts / mode_permission_policy): which tool
    intents exist, what they cost in permission actions, what an approval binds,
    and where receipts live. Providers never grow their own gate.
  * The CREDENTIAL STORE: the only home for secrets and OAuth handles. An
    adapter reads its blob by name and never logs or returns its contents.

TRANSPORT OUTCOME LAW (revision 4 — the dispatch/read split)
-------------------------------------------------------------
A mutation (a state-changing request) and a read fail differently, and
conflating them is how accepted sends reopen and dead reads become "empty"
mailboxes. The client therefore exposes two operations over one wire attempt:

  api_mutation() -> MutationOutcome, one of:
    accepted   the provider SPOKE a 2xx. Terminal and sticky: no later body
               timeout, truncated read, invalid UTF-8, non-object JSON or
               receipt-write failure may downgrade it. Provider ids are
               best-effort from the payload when it is a readable object.
    uncertain  the request may have been transmitted but no status arrived
               (header timeout, wrapped connection reset, dropped link). NEVER
               retryable as if unsent; maps to delivery_unknown.
    unsent     PROVEN nothing was sent: the connection never opened (DNS
               failure, connection refused). Retryable as an ordinary failure.
    Explicit refusals (non-2xx) raise ProviderAccountError(provider_refused)
    carrying the spoken status. A 401 means the request was NOT processed: it
    earns exactly ONE genuine refresh (a new token; under a PrincipalBinding
    that token is verified before it carries anything), and a second 401 is
    needs_reauthorization. Uncertain and accepted dispatches are never retried.
  api_read() -> dict (valid typed payload) or raises read_failed: an accepted
    HTTP request is not a readable mailbox result. A body timeout, malformed
    JSON, invalid UTF-8 or a non-object payload is a FAILED read, never a
    successful empty one.

The request-phase classifier is phase-based, not exception-name-based: any
failure before a response object exists is uncertain UNLESS the cause chain
proves the connection never opened. Everything after the status line is
response-phase and classified by the status itself.

AUTHENTICATED DISPATCH LAW (revision 5)
---------------------------------------
Local metadata names the mailbox an account slot is SUPPOSED to reach; only the
provider's own profile, read with the exact bearer token a request carries,
proves which mailbox that token authenticates. Every request that carries an
approved effect (the send, the parent lookups it depends on, a reconciliation
read) runs under a PrincipalBinding:

  * before a request leaves, the token it will carry must have been verified
    against the bound principal (once per token, not once per request);
  * a token minted later (expiry refresh, 401 recovery, a reloaded credential)
    is a new credential event and is verified again before it carries anything;
  * a profile that cannot be read (timeout, refusal, malformed body) or that
    names no identity is `identity_unverified`: fail closed, nothing is sent;
  * a profile that conclusively names another principal is `needs_reapproval`.

Metadata never substitutes for that evidence. Rotating the refresh token of the
SAME principal is not a repoint: the principal, never the grant string, is the
identity.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core import credential_store

# The refusals a PrincipalBinding raises. Callers must surface them as they are,
# never absorb them as an ordinary lookup failure or an empty result.
BINDING_REFUSALS = frozenset({"identity_unverified", "needs_reapproval"})


class ProviderAccountError(RuntimeError):
    """An account-level condition the transport cannot resolve itself.

    `kind` is one of: needs_setup (no account blob), needs_reauthorization
    (revoked/expired OAuth grant), provider_refused (a final provider-side
    refusal), read_failed (a read whose payload could not be retrieved as valid
    typed data), transport_failed (a proven pre-wire failure), identity_unverified
    (the provider's profile could not confirm which mailbox the credential
    authenticates) and needs_reapproval (the credential conclusively
    authenticates a different mailbox than the one bound). These surface as
    structured, actionable results — never as fake success and never with
    secrets in the message."""

    def __init__(self, kind: str, message: str, *, provider: str = "", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.status = status


@dataclass(frozen=True)
class ProviderCapability:
    search: bool
    open_message: bool
    open_thread: bool
    send_threaded: bool


@dataclass(frozen=True)
class MutationOutcome:
    """The durable dispatch evidence for one mutating request."""

    kind: str                 # "accepted" | "uncertain" | "unsent"
    status: int | None = None
    payload: Any = None       # parsed JSON when readable; ANY shape, maybe None
    note: str = ""

    @property
    def accepted(self) -> bool:
        return self.kind == "accepted"


@dataclass
class _Envelope:
    """One wire attempt's raw truth, before operation-specific reading."""

    status: int | None = None
    accepted: bool = False
    payload: Any = None
    body_error: str = ""
    request_error: BaseException | None = None
    proven_unsent: bool = False


class _AuthRejectedError(Exception):
    """A spoken 401 for ONE bearer token: the provider refused the request before
    processing it. Internal to the transport, which owns credential recovery."""

    def __init__(self, token: str, http_error: urllib.error.HTTPError) -> None:
        super().__init__("401")
        self.token = token
        self.http_error = http_error


def _proven_never_connected(exc: BaseException) -> bool:
    """True only when the cause chain proves the connection never opened, so
    nothing could have been transmitted. DNS failure and connection refusal are
    that proof; a reset or a timeout is NOT (the request may have flushed)."""
    seen: list[BaseException] = [exc]
    while seen:
        current = seen.pop()
        if isinstance(current, (socket.gaierror, ConnectionRefusedError)):
            return True
        for attr in ("reason", "__cause__"):
            nxt = getattr(current, attr, None)
            if isinstance(nxt, BaseException) and nxt is not current:
                seen.append(nxt)
    return False


def _read_body(response: Any) -> tuple[str, str]:
    """(text, error) — error is a short reason when the body could not be read
    or decoded; never raises."""
    try:
        return response.read().decode("utf-8"), ""
    except Exception as exc:  # IncompleteRead, timeouts, connection drops
        return "", f"{type(exc).__name__}"


def identity_parts(identity: str) -> tuple[str, str]:
    """(alias, principal) from a local account binding `alias[|oauth-meta:principal]`."""
    alias, sep, tail = str(identity or "").partition("|oauth")
    principal = tail.split(":", 1)[1] if sep and ":" in tail else ""
    return alias.strip().lower(), principal.strip().lower()


class PrincipalBinding:
    """The authenticated-dispatch contract for one provider operation.

    `bound_identity` is the local binding the approval (or the account slot)
    names; `read_principals(token)` reads the provider's own profile WITH that
    token and returns the identity names it reports, raising on any read
    failure (a spoken 401 as _AuthRejectedError, so the transport can recover the
    credential and verify the new one)."""

    def __init__(self, *, provider: str, bound_identity: str, alias: str,
                 read_principals: Callable[[str], list[str]]) -> None:
        self.provider = provider
        self.bound_alias, self.bound_principal = identity_parts(bound_identity)
        self.alias = str(alias or "").strip().lower()
        self._read_principals = read_principals
        self._verified: set[str] = set()
        self.verified_principal = ""

    def check(self, token: str) -> None:
        """Return only when `token` is verified as the bound principal."""
        fingerprint = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        if fingerprint in self._verified:
            return
        if not self.bound_principal:
            raise ProviderAccountError(
                "identity_unverified",
                f"This {self.provider} account records no authenticated mailbox to bind the send to, "
                "so nothing was sent. Reconnect the account through email setup.",
                provider=self.provider,
            )
        if self.alias != self.bound_alias:
            raise ProviderAccountError(
                "needs_reapproval",
                f"The {self.provider} account's sending address is now {self.alias!r}, not the reviewed "
                f"{self.bound_alias!r}; nothing was sent. Re-review and re-approve before sending.",
                provider=self.provider,
            )
        try:
            names = {str(name).strip().lower() for name in self._read_principals(token)
                     if str(name or "").strip()}
        except _AuthRejectedError:
            raise
        except ProviderAccountError as exc:
            detail = exc.kind + (f" {exc.status}" if exc.status else "")
            raise ProviderAccountError(
                "identity_unverified",
                f"{self.provider} could not confirm which mailbox this credential authenticates "
                f"({detail}); nothing was sent. Try again once the account profile is readable, or "
                "reconnect the account.",
                provider=self.provider, status=exc.status,
            ) from exc
        except Exception as exc:  # a refused or unreachable profile read
            raise ProviderAccountError(
                "identity_unverified",
                f"{self.provider} could not confirm which mailbox this credential authenticates "
                f"({type(exc).__name__}); nothing was sent. Try again once the account profile is "
                "readable, or reconnect the account.",
                provider=self.provider, status=getattr(exc, "code", None),
            ) from exc
        if not names:
            raise ProviderAccountError(
                "identity_unverified",
                f"{self.provider}'s profile for this credential names no mailbox identity, so the "
                "mailbox cannot be confirmed; nothing was sent.",
                provider=self.provider,
            )
        if self.bound_principal not in names:
            raise ProviderAccountError(
                "needs_reapproval",
                f"The {self.provider} credential behind this account now authenticates "
                f"{sorted(names)[0]!r}, not the reviewed mailbox {self.bound_principal!r}; nothing was "
                "sent. Re-review and re-approve before sending.",
                provider=self.provider,
            )
        self._verified.add(fingerprint)
        self.verified_principal = self.bound_principal

    def evidence(self) -> dict[str, Any]:
        return {
            "verified_principal": self.verified_principal,
            "principal_verifications": len(self._verified),
            "identity_verification": ("provider_profile_per_token" if self.verified_principal
                                      else "not_verified"),
        }


class _OAuthClient:
    """Minimal OAuth2 refresh against a token endpoint (RFC 6749 §6).

    The refresh token, client id and secret live in the credential store as one
    JSON handle; only the short-lived access token is kept in memory. A revoked
    or expired refresh grant raises ProviderAccountError(needs_reauthorization)
    with the provider's own error description — the account owner must reconnect
    through setup; nothing here can or should silently re-consent."""

    def __init__(self, *, provider: str, token_url: str, client_id: str, client_secret: str,
                 refresh_token: str, scopes: str, on_rotation=None):
        self.provider = provider
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._scopes = scopes
        self._access_token = ""
        self._expires_at = 0.0
        # Serializes the refresh a 401 earns, so concurrent requests that saw the same
        # token refused share ONE refresh.
        self._refresh_lock = threading.Lock()
        # Optional callback(new_refresh_token: str): providers may ROTATE the refresh
        # token on use (RFC 6749 §6); the rotation must be persisted or the next
        # restart loses the grant. The adapter wires this to the credential store.
        self._on_rotation = on_rotation

    def access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._access_token and time.time() < self._expires_at - 30:
            return self._access_token
        from urllib.parse import urlencode

        form: dict[str, str] = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }
        if self._scopes:
            form["scope"] = self._scopes
        request = urllib.request.Request(
            self._token_url, data=urlencode(form).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30,
                                        context=ssl.create_default_context()) as response:
                text, error = _read_body(response)
            if error or not text.strip():
                raise OSError(error or "empty token response")
            payload = json.loads(text)
        except urllib.error.HTTPError as exc:
            detail = ""
            with contextlib.suppress(Exception):
                detail = str(json.loads(exc.read().decode("utf-8")).get("error_description") or "")
            if exc.code in (400, 401, 403):
                raise ProviderAccountError(
                    "needs_reauthorization",
                    f"{self.provider} authorization was refused ({exc.code}"
                    f"{': ' + detail if detail else ''}); reconnect the account via email setup.",
                    provider=self.provider, status=exc.code,
                ) from exc
            raise ProviderAccountError(
                "transport_failed", f"{self.provider} token endpoint error {exc.code}.",
                provider=self.provider, status=exc.code,
            ) from exc
        except ProviderAccountError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ProviderAccountError(
                "transport_failed", f"{self.provider} token endpoint unreachable ({type(exc).__name__}).",
                provider=self.provider,
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderAccountError("needs_reauthorization",
                                       f"{self.provider} returned an unshaped token response.",
                                       provider=self.provider)
        self._access_token = str(payload.get("access_token") or "")
        self._expires_at = time.time() + float(payload.get("expires_in") or 3600)
        if not self._access_token:
            raise ProviderAccountError("needs_reauthorization",
                                       f"{self.provider} returned no access token.", provider=self.provider)
        rotated = str(payload.get("refresh_token") or "")
        if rotated and rotated != self._refresh_token:
            self._refresh_token = rotated
            if callable(self._on_rotation):
                with contextlib.suppress(Exception):
                    self._on_rotation(rotated)
        return self._access_token

    # -- one wire attempt, carrying exactly one credential -------------------
    def _attempt(self, method: str, url: str, token: str, *, body: bytes | None = None,
                 content_type: str = "application/json") -> _Envelope:
        # Content-Type belongs to requests WITH a body; a bodyless GET must not
        # look like a JSON POST to strict provider boundaries.
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=60,
                                              context=ssl.create_default_context())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise _AuthRejectedError(token, exc) from exc
            # Any other status is the provider's spoken, final refusal of THIS request.
            detail = ""
            with contextlib.suppress(Exception):
                detail = exc.read().decode("utf-8", "replace")[:300]
            raise ProviderAccountError(
                "provider_refused", f"{self.provider} call failed ({exc.code}): {detail}",
                provider=self.provider, status=exc.code,
            ) from exc
        except Exception as exc:  # request phase: no status was received
            return _Envelope(request_error=exc, proven_unsent=_proven_never_connected(exc))
        # Response phase: the provider spoke this status, whatever the body does.
        envelope = _Envelope()
        with response:
            envelope.status = int(getattr(response, "status", None)
                                  or getattr(response, "code", 200) or 200)
            envelope.accepted = 200 <= envelope.status < 300
            if not envelope.accepted:
                detail = ""
                with contextlib.suppress(Exception):
                    detail = response.read().decode("utf-8", "replace")[:300]
                raise ProviderAccountError(
                    "provider_refused",
                    f"{self.provider} call failed ({envelope.status}): {detail}",
                    provider=self.provider, status=envelope.status,
                )
            text, error = _read_body(response)
            if error:
                envelope.body_error = error
            elif not text.strip():
                envelope.body_error = "empty-body"
            else:
                try:
                    envelope.payload = json.loads(text)
                except ValueError:
                    envelope.body_error = "malformed-json"
        return envelope

    def _perform(self, method: str, url: str, *, payload: dict[str, Any] | None = None,
                 raw_body: bytes | None = None, content_type: str = "application/json",
                 binding: PrincipalBinding | None = None) -> _Envelope:
        body = raw_body
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        token = self.access_token()
        refreshed = False
        while True:
            try:
                if binding is not None:
                    # The credential this request carries is verified BEFORE it
                    # carries anything; a token already verified is not re-read.
                    binding.check(token)
                return self._attempt(method, url, token, body=body, content_type=content_type)
            except _AuthRejectedError as rejection:
                # A spoken 401 (on the request or on its identity read) means nothing was
                # processed. It earns ONE genuine refresh, and the loop verifies the new
                # token before it carries the request. A second 401 is a revoked grant.
                if refreshed:
                    raise ProviderAccountError(
                        "needs_reauthorization",
                        f"{self.provider} refused the refreshed credential as well (401), so the "
                        "account's access appears revoked; the refused request was not processed. "
                        "Reconnect the account through email setup.",
                        provider=self.provider, status=401,
                    ) from rejection.http_error
                refreshed = True
                token = self._refresh_after_rejection(rejection.token)

    def _refresh_after_rejection(self, rejected: str) -> str:
        """ONE genuine refresh for a token the provider refused. Concurrent requests that
        saw the same token refused share a single refresh instead of each spending one."""
        with self._refresh_lock:
            if self._access_token and self._access_token != rejected:
                return self._access_token
            self._access_token = ""
            self._expires_at = 0.0
            fresh = self.access_token(force_refresh=True)
        if fresh == rejected:
            raise ProviderAccountError(
                "needs_reauthorization",
                f"{self.provider} re-issued the credential it had just refused (401); reconnect the "
                "account through email setup.",
                provider=self.provider, status=401,
            )
        return fresh

    @staticmethod
    def _typed(envelope: _Envelope, provider: str) -> dict[str, Any]:
        if envelope.request_error is not None:
            raise ProviderAccountError(
                "read_failed",
                f"{provider} read failed before a response "
                f"({type(envelope.request_error).__name__}).",
                provider=provider,
            )
        if envelope.body_error:
            raise ProviderAccountError(
                "read_failed",
                f"{provider} returned an unreadable response body ({envelope.body_error}); "
                "the mailbox result is unknown, not empty.",
                provider=provider, status=envelope.status,
            )
        if not isinstance(envelope.payload, dict):
            raise ProviderAccountError(
                "read_failed",
                f"{provider} returned {type(envelope.payload).__name__} instead of an object; "
                "the mailbox result is unknown, not empty.",
                provider=provider, status=envelope.status,
            )
        return envelope.payload

    # -- operation surfaces ----------------------------------------------------
    def api_mutation(self, method: str, url: str, *, payload: dict[str, Any] | None = None,
                     raw_body: bytes | None = None, content_type: str = "application/json",
                     binding: PrincipalBinding | None = None) -> MutationOutcome:
        """Dispatch a state-changing request. NEVER treats uncertainty as unsent
        and never lets a post-acceptance body failure downgrade a known
        acceptance. Only auth/refusal/identity errors raise."""
        envelope = self._perform(method, url, payload=payload, raw_body=raw_body,
                                 content_type=content_type, binding=binding)
        if envelope.request_error is not None:
            if envelope.proven_unsent:
                return MutationOutcome("unsent", note=type(envelope.request_error).__name__)
            return MutationOutcome("uncertain", note=type(envelope.request_error).__name__)
        if envelope.accepted:
            note = ""
            if envelope.body_error:
                note = f"accepted (status {envelope.status}); body unreadable: {envelope.body_error}"
            elif not isinstance(envelope.payload, dict):
                note = (f"accepted (status {envelope.status}); payload shape: "
                        f"{type(envelope.payload).__name__}")
            return MutationOutcome("accepted", status=envelope.status,
                                   payload=envelope.payload, note=note)
        return MutationOutcome("unsent", status=envelope.status, note="non-2xx")

    def api_read(self, method: str, url: str, *, binding: PrincipalBinding | None = None) -> dict[str, Any]:
        """Retrieve typed data. An accepted request whose body cannot be read,
        decoded or parsed as a JSON OBJECT is a FAILED read — never a
        successful empty result."""
        return self._typed(self._perform(method, url, binding=binding), self.provider)

    def read_with_token(self, method: str, url: str, token: str) -> dict[str, Any]:
        """One typed read carrying exactly `token`: no refresh and no binding. The
        primitive a PrincipalBinding verifies a credential with."""
        return self._typed(self._attempt(method, url, token), self.provider)


def _load_oauth_handle(provider: str, account: str) -> dict[str, Any]:
    raw = credential_store.get_credential(f"email.oauth.{provider}.{account}")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def provider_for_account(creds: dict[str, Any] | None) -> str:
    """The transport an account blob names ('gmail' | 'graph' | 'icloud' | 'imap').

    Unknown values are REFUSED (never silently treated as imap): a mistyped
    provider must not fall back to password SMTP for an OAuth account."""
    provider = str((creds or {}).get("provider") or "imap").strip().lower()
    if provider not in {"gmail", "graph", "icloud", "imap"}:
        raise ProviderAccountError(
            "needs_setup",
            f"unknown email provider {provider!r} on this account; expected gmail, graph, icloud or imap.",
        )
    return provider


__all__ = [
    "BINDING_REFUSALS",
    "MutationOutcome",
    "PrincipalBinding",
    "ProviderAccountError",
    "ProviderCapability",
    "identity_parts",
    "provider_for_account",
]
